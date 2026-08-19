"""Deck-scoped authored chapter plan: what a chaptered deck decides once.

Two things come out of the plan and they have to describe one deck: the outline
the slides are authored from, and the indices the plan reports. Most of what is
checked here is that pairing — a divider page and the plan entry that claims it
must land on the same slide, and a page the plan did not insert must keep the
role its own text derives.
"""

import asyncio

import pytest

from models.authored_chapter_plan import (
    AGENDA_ROLE,
    MIN_SLIDES_FOR_CHAPTER_PLAN,
    SECTION_ROLE,
    AuthoredChapter,
    AuthoredChapterDraft,
    AuthoredChapterPlan,
    AuthoredChapterPlanDraft,
)
from models.authored_header_contract import (
    BREADCRUMB_MIN_EYEBROW_CHARS,
    DEFAULT_EXCLUDED_ROLES,
    AuthoredHeaderContract,
    HeaderEyebrowSpec,
    default_header_contract,
)
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from services import authored_deck_chapter_plan as chapter_plan_service
from services.authored_deck_chapter_plan import (
    expand_outline_with_chapters,
    load_chapter_plan,
    resolve_chapter_plan,
)
from utils.authored_header_prompt import render_header_contract
from utils.authored_styles import reference_images_for_role
from utils.llm_calls.author_chapter_plan import (
    coerce_chapter_plan_draft,
    slide_heading,
)
from utils.llm_calls.author_deck import plan_deck_roles, prepare_authored_deck
from utils.llm_calls.author_header_contract import coerce_header_contract
from utils.llm_calls.author_slide_prompt import (
    author_prompt,
    chapter_marker,
    structure_role_brief,
)
from utils.llm_calls.author_slide import Brand


def _outline(count: int) -> PresentationOutlineModel:
    return PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"## 슬라이드 {i}") for i in range(count)]
    )


def _draft(*, agenda: bool = False, **overrides) -> AuthoredChapterPlanDraft:
    return AuthoredChapterPlanDraft(
        include_agenda=agenda,
        agenda_title="목차" if agenda else "",
        chapters=(
            AuthoredChapterDraft(numeral="Ⅰ", title="사업개요", first_slide_index=1),
            AuthoredChapterDraft(numeral="Ⅱ", title="제안개요", first_slide_index=5),
            AuthoredChapterDraft(numeral="Ⅲ", title="기대효과", first_slide_index=9),
        ),
        **overrides,
    )


def _plan() -> AuthoredChapterPlan:
    return AuthoredChapterPlan(
        chapters=(
            AuthoredChapter(numeral="Ⅰ", title="사업개요", start_index=1),
            AuthoredChapter(numeral="Ⅱ", title="제안개요", start_index=6),
        )
    )


# --- the outline and the plan describe one deck --------------------------------


def test_expansion_puts_every_divider_where_its_chapter_claims_it():
    expansion = expand_outline_with_chapters(_outline(12), _draft())
    assert expansion is not None
    slides = expansion.outline.slides
    assert len(slides) == 15
    assert expansion.inserted == 3
    for chapter in expansion.plan.chapters:
        divider = slides[chapter.start_index].content
        assert divider.startswith(f"## {chapter.numeral}. {chapter.title}")
        assert expansion.plan.role_at(chapter.start_index) == SECTION_ROLE


def test_a_divider_lists_the_pages_of_its_own_chapter_and_no_others():
    source = PresentationOutlineModel(
        slides=[SlideOutlineModel(content=f"## 항목 {i}") for i in range(12)]
    )
    expansion = expand_outline_with_chapters(source, _draft())
    assert expansion is not None
    second = expansion.plan.chapters[1]
    divider = expansion.outline.slides[second.start_index].content
    # Chapter II opens on source slide 5 and runs to source slide 8; the pages
    # of chapter III and the closing page belong to neither.
    assert "항목 5" in divider and "항목 8" in divider
    assert "항목 9" not in divider
    assert "항목 11" not in divider


def test_the_agenda_sits_behind_the_cover_and_lists_every_chapter():
    expansion = expand_outline_with_chapters(_outline(12), _draft(agenda=True))
    assert expansion is not None
    assert expansion.plan.agenda_index == 1
    agenda = expansion.outline.slides[1].content
    assert agenda.startswith("## 목차")
    for label in expansion.plan.outline_labels():
        assert label in agenda
    assert expansion.plan.role_at(1) == AGENDA_ROLE
    # The cover is never displaced by front matter inserted behind it.
    assert expansion.outline.slides[0].content == "## 슬라이드 0"


def test_a_deck_that_already_lists_its_chapters_is_not_given_a_second_agenda():
    draft = AuthoredChapterPlanDraft(
        include_agenda=False,
        existing_agenda_index=1,
        chapters=(
            AuthoredChapterDraft(numeral="Ⅰ", title="사업개요", first_slide_index=2),
            AuthoredChapterDraft(numeral="Ⅱ", title="제안개요", first_slide_index=6),
            AuthoredChapterDraft(numeral="Ⅲ", title="기대효과", first_slide_index=9),
        ),
    )
    expansion = expand_outline_with_chapters(_outline(12), draft)
    assert expansion is not None
    # Three dividers and no agenda page: the deck already had one.
    assert expansion.inserted == 3
    assert expansion.plan.agenda_index == 1
    assert expansion.plan.role_at(1) == AGENDA_ROLE
    # The adopted page keeps its own wording rather than being rewritten.
    assert expansion.outline.slides[1].content == "## 슬라이드 1"


def test_an_adopted_agenda_index_tracks_the_slides_inserted_before_it():
    draft = AuthoredChapterPlanDraft(
        existing_agenda_index=2,
        chapters=(
            AuthoredChapterDraft(numeral="Ⅰ", title="개요", first_slide_index=4),
            AuthoredChapterDraft(numeral="Ⅱ", title="제안", first_slide_index=8),
        ),
    )
    expansion = expand_outline_with_chapters(_outline(12), draft)
    assert expansion is not None
    # Nothing is inserted ahead of source slide 2, so it keeps its index.
    assert expansion.plan.agenda_index == 2
    assert expansion.outline.slides[2].content == "## 슬라이드 2"
    assert expansion.plan.chapters[0].start_index == 4


def test_the_two_agenda_fields_never_both_hold():
    draft = coerce_chapter_plan_draft(
        {
            "include_agenda": True,
            "agenda_title": "목차",
            "existing_agenda_index": 1,
            "chapters": [
                {"numeral": "Ⅰ", "title": "가", "first_slide_index": 3},
                {"numeral": "Ⅱ", "title": "나", "first_slide_index": 7},
            ],
        },
        slide_count=12,
    )
    assert draft.existing_agenda_index == 1
    assert draft.include_agenda is False


def test_an_adopted_agenda_outside_the_front_matter_is_dropped():
    base = {
        "existing_agenda_index": None,
        "chapters": [
            {"numeral": "Ⅰ", "title": "가", "first_slide_index": 3},
            {"numeral": "Ⅱ", "title": "나", "first_slide_index": 7},
        ],
    }
    for bad in (0, 5, 40):
        draft = coerce_chapter_plan_draft(
            {**base, "existing_agenda_index": bad}, slide_count=12
        )
        assert draft.existing_agenda_index is None, bad
    good = coerce_chapter_plan_draft(
        {**base, "existing_agenda_index": 2}, slide_count=12
    )
    assert good.existing_agenda_index == 2


def test_a_draft_with_too_few_usable_chapters_leaves_the_deck_alone():
    draft = AuthoredChapterPlanDraft(
        chapters=(
            AuthoredChapterDraft(numeral="Ⅰ", title="개요", first_slide_index=1),
        )
    )
    assert expand_outline_with_chapters(_outline(12), draft) is None


# --- what the plan reports per slide -------------------------------------------


def test_only_the_pages_of_a_chapter_carry_its_prefix():
    plan = _plan()
    # Cover: front matter, no chapter has started.
    assert plan.eyebrow_prefix_at(0) is None
    # The divider itself announces the chapter rather than being filed under it.
    assert plan.eyebrow_prefix_at(1) is None
    assert plan.eyebrow_prefix_at(2) == "Ⅰ. 사업개요 > "
    assert plan.eyebrow_prefix_at(5) == "Ⅰ. 사업개요 > "
    assert plan.eyebrow_prefix_at(7) == "Ⅱ. 제안개요 > "


def test_the_agenda_page_borrows_no_chapters_number():
    plan = AuthoredChapterPlan(
        agenda_index=1,
        chapters=(
            AuthoredChapter(numeral="Ⅰ", title="사업개요", start_index=2),
            AuthoredChapter(numeral="Ⅱ", title="제안개요", start_index=6),
        ),
    )
    assert plan.eyebrow_prefix_at(1) is None
    assert plan.role_at(1) == AGENDA_ROLE


def test_a_plan_whose_agenda_falls_inside_a_chapter_is_refused():
    with pytest.raises(ValueError):
        AuthoredChapterPlan(
            agenda_index=4,
            chapters=(
                AuthoredChapter(numeral="Ⅰ", title="개요", start_index=2),
                AuthoredChapter(numeral="Ⅱ", title="제안", start_index=6),
            ),
        )


def test_a_plan_with_repeated_numerals_or_indices_is_refused():
    with pytest.raises(ValueError):
        AuthoredChapterPlan(
            chapters=(
                AuthoredChapter(numeral="Ⅰ", title="개요", start_index=2),
                AuthoredChapter(numeral="Ⅰ", title="제안", start_index=6),
            ),
        )
    with pytest.raises(ValueError):
        AuthoredChapterPlan(
            chapters=(
                AuthoredChapter(numeral="Ⅰ", title="개요", start_index=2),
                AuthoredChapter(numeral="Ⅱ", title="제안", start_index=2),
            ),
        )


# --- roles the plan forces ------------------------------------------------------


def test_an_inserted_page_takes_the_role_the_deck_gave_it_not_its_keywords():
    source = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content=f"## 항목 {i}") for i in range(12)
        ]
    )
    draft = AuthoredChapterPlanDraft(
        agenda_title="",
        chapters=(
            # A chapter whose own name would otherwise derive ARCHITECTURE.
            AuthoredChapterDraft(numeral="Ⅰ", title="시스템 구조", first_slide_index=3),
            AuthoredChapterDraft(numeral="Ⅱ", title="기대효과", first_slide_index=8),
        ),
    )
    expansion = expand_outline_with_chapters(source, draft)
    assert expansion is not None
    roles = plan_deck_roles(expansion.outline, expansion.plan)
    for chapter in expansion.plan.chapters:
        assert roles[chapter.start_index] == SECTION_ROLE
    assert roles[0] == "COVER"
    assert roles[-1] == "CLOSING"


def test_without_a_plan_role_derivation_is_exactly_what_it_was():
    outline = _outline(12)
    assert plan_deck_roles(outline) == plan_deck_roles(outline, None)


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


def test_a_chapter_divider_skips_the_header_band_like_the_cover():
    assert SECTION_ROLE in DEFAULT_EXCLUDED_ROLES
    contract = default_header_contract()
    assert not contract.applies_to_role(SECTION_ROLE)
    assert contract.applies_to_role(AGENDA_ROLE)


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


def test_only_the_inserted_roles_carry_a_structural_brief():
    assert structure_role_brief("PILLARS") == ""
    assert "CHAPTER DIVIDER" in structure_role_brief(SECTION_ROLE)
    assert "TABLE OF CONTENTS" in structure_role_brief(AGENDA_ROLE)


def test_the_authoring_plan_carries_one_prefix_per_slide():
    expansion = expand_outline_with_chapters(_outline(12), _draft(agenda=True))
    assert expansion is not None
    plan = prepare_authored_deck(
        expansion.outline,
        Brand(topic="주제", language="Korean"),
        None,
        header_contract=default_header_contract(chapter_breadcrumb=True),
        chapter_plan=expansion.plan,
    )
    assert len(plan.chapter_prefixes) == len(expansion.outline.slides)
    first = expansion.plan.chapters[0]
    assert plan.roles[first.start_index] == SECTION_ROLE
    assert plan.chapter_prefixes[first.start_index] is None
    assert plan.chapter_prefixes[first.start_index + 1] == "Ⅰ. 사업개요 > "


def test_a_deck_with_no_plan_carries_no_prefixes_at_all():
    plan = prepare_authored_deck(
        _outline(12),
        Brand(topic="주제", language="Korean"),
        None,
        header_contract=default_header_contract(),
    )
    assert plan.chapter_prefixes == ()


# --- reference images for the two new roles -------------------------------------


class _Style:
    def __init__(self, **images):
        self.reference_images = images


def test_a_divider_falls_back_to_the_cover_reference_a_style_already_has():
    style = _Style(cover=("cover.png",), content=("content.png",))
    assert reference_images_for_role(style, SECTION_ROLE) == ("cover.png",)
    assert reference_images_for_role(style, AGENDA_ROLE) == ("content.png",)


def test_a_style_that_art_directs_the_two_roles_is_preferred_over_the_fallback():
    style = _Style(
        cover=("cover.png",),
        content=("content.png",),
        section=("section.png",),
        agenda=("agenda.png",),
    )
    assert reference_images_for_role(style, SECTION_ROLE) == ("section.png",)
    assert reference_images_for_role(style, AGENDA_ROLE) == ("agenda.png",)


# --- coercion drops what the deck cannot carry ----------------------------------


def test_a_chapter_on_the_cover_or_the_closing_is_dropped_not_honoured():
    payload = {
        "chapters": [
            {"numeral": "Ⅰ", "title": "표지", "first_slide_index": 0},
            {"numeral": "Ⅱ", "title": "본문", "first_slide_index": 4},
            {"numeral": "Ⅲ", "title": "마무리", "first_slide_index": 11},
        ]
    }
    draft = coerce_chapter_plan_draft(payload, slide_count=12)
    assert [chapter.first_slide_index for chapter in draft.chapters] == [4]


def test_two_chapters_claiming_one_slide_keep_only_the_first():
    payload = {
        "chapters": [
            {"numeral": "Ⅰ", "title": "가", "first_slide_index": 3},
            {"numeral": "Ⅱ", "title": "나", "first_slide_index": 3},
            {"numeral": "Ⅲ", "title": "다", "first_slide_index": 7},
        ]
    }
    draft = coerce_chapter_plan_draft(payload, slide_count=12)
    assert [chapter.numeral for chapter in draft.chapters] == ["Ⅰ", "Ⅲ"]


def test_two_chapters_numbered_alike_do_not_cost_the_deck_its_structure():
    payload = {
        "chapters": [
            {"numeral": "Ⅱ", "title": "가", "first_slide_index": 3},
            {"numeral": "Ⅱ", "title": "나", "first_slide_index": 6},
            {"numeral": "Ⅲ", "title": "다", "first_slide_index": 9},
        ]
    }
    draft = coerce_chapter_plan_draft(payload, slide_count=14)
    assert [chapter.numeral for chapter in draft.chapters] == ["Ⅱ", "Ⅲ"]
    # And what survives still expands into a plan the model would have refused.
    assert expand_outline_with_chapters(_outline(14), draft) is not None


def test_a_marker_copied_with_its_separator_is_not_printed_twice():
    # A source that writes "Ⅱ. 제안개요" reports the marker the way the page
    # reads it, separator included. Every renderer here adds the separator, so
    # keeping it would print "Ⅱ.. 제안개요" on every page of that chapter.
    for reported, stored in (
        ("Ⅱ.", "Ⅱ"),
        ("Ⅵ. ", "Ⅵ"),
        ("2)", "2"),
        ("제1장", "제1장"),
    ):
        chapter = AuthoredChapter(numeral=reported, title="제안개요", start_index=2)
        assert chapter.numeral == stored
        assert chapter.prefix == f"{stored}. 제안개요"
    draft = AuthoredChapterDraft(numeral="Ⅲ.", title="수행방안", first_slide_index=3)
    assert draft.numeral == "Ⅲ"


def test_two_chapters_differing_only_by_that_separator_are_one_numeral():
    # Normalisation happens before the uniqueness check, so "Ⅱ" and "Ⅱ." are
    # the same chapter marker rather than two that merely look alike.
    with pytest.raises(ValueError):
        AuthoredChapterPlan(
            chapters=(
                AuthoredChapter(numeral="Ⅱ", title="가", start_index=2),
                AuthoredChapter(numeral="Ⅱ.", title="나", start_index=6),
            )
        )


def test_slide_heading_reads_the_markdown_title_a_slide_leads_with():
    assert slide_heading("## 목표 아키텍처\n\n본문") == "목표 아키텍처"
    assert slide_heading("본문만 있는 슬라이드") == "본문만 있는 슬라이드"
    assert slide_heading("") == "(untitled)"


# --- when the deck is left alone ------------------------------------------------


def test_a_short_deck_is_never_chaptered_and_costs_no_model_call(monkeypatch):
    calls = 0

    async def never(**_options):
        nonlocal calls
        calls += 1
        raise AssertionError("a short deck must not reach the planning model")

    monkeypatch.setattr(chapter_plan_service, "generate_chapter_plan", never)
    result = asyncio.run(
        resolve_chapter_plan(
            _outline(MIN_SLIDES_FOR_CHAPTER_PLAN - 1),
            Brand(topic="주제", language="Korean"),
            None,
        )
    )
    assert result is None
    assert calls == 0


def test_an_unreadable_persisted_plan_is_read_as_no_plan():
    assert load_chapter_plan(None) is None
    assert load_chapter_plan({}) is None
    assert load_chapter_plan({"chapterPlan": {"chapters": []}}) is None
    stored = {"chapterPlan": _plan().model_dump(mode="json")}
    assert load_chapter_plan(stored) == _plan()
