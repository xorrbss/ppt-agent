"""The authoring prompt's illustration slot contract.

The slot is only rendered for illustration-enabled styles, so every assertion
here builds the design system twice: once for a plain style and once for a style
that carries an ``illustration_prompt``.

HOW MANY slides carry a picture is no longer decided in this prose. A slide is
authored alone and in parallel and cannot see its siblings, so a per-slide
judgement of that question oscillated between 4/77 and 73/77 on the same deck;
the count now comes from a deck-scoped plan handed to each slide as
``illustration_allowed``. What this file pins is the part that is a contract with
other components: the slot syntax, the geometry that lets the layout survive the
runtime deleting the tag, and the label budget.

The two numbers checked below -- at most 6 labels, every label verbatim from the
slide's own CONTENT -- are shared with the illustration runtime, which enforces
the same limits when it composes the image request. Changing them here without
changing them there silently breaks the pairing.
"""

from types import SimpleNamespace

from utils.llm_calls import author_slide_rules
from utils.llm_calls.author_slide import Brand, build_design_system


def _plain_design_system() -> str:
    return build_design_system(
        Brand(topic="주제"), SimpleNamespace(id="editorial", brief="- Editorial")
    )


def _illustrated_design_system() -> str:
    return build_design_system(
        Brand(topic="주제"),
        SimpleNamespace(
            id="iso", brief="- Iso", illustration_prompt="isometric style"
        ),
    )


def test_the_slot_syntax_is_the_only_way_to_request_an_image():
    rules = _illustrated_design_system()

    assert '`<img data-illust-prompt="SCENE" style="...">`' in rules
    assert "the ONLY way to ask for a generated picture is ONE" in rules
    assert "never more than one" in rules
    # The scene description is what the illustration runtime forwards, so it must
    # name this slide's own subject rather than a mood.
    assert "describe in English the specific subject THIS slide argues about" in rules


def test_slot_geometry_survives_the_runtime_deleting_the_tag():
    rules = _illustrated_design_system()

    assert "SLOT GEOMETRY IS MANDATORY" in rules
    assert "width AND height in px (or an absolute inset box)" in rules
    assert "keep width/height between 1.3 and 2.0" in rules
    assert "no width:100% with height:100%, no inset:0" in rules
    assert "never\n  place it behind or under the text" in rules
    assert "no external URLs, no data URIs" in rules


def test_dom_only_figure_kinds_are_stated_even_without_an_illustration_style():
    # This is the always-on half of the picture-vs-DOM rule and it survived the
    # deleted tier prose: it lives in the design system's PPTX-NATIVE SAFETY
    # section, so a deck with no illustration slot still must not bake a table
    # or a KPI into a picture.
    rules = _plain_design_system()

    assert "ALWAYS stay HTML+CSS DOM" in rules
    assert "Tables, KPI figures, org charts, gantt bars and schedule rails" in rules
    assert "cannot be\n    corrected, searched or re-used" in rules


def test_image_labels_are_capped_at_six_and_copied_verbatim_from_content():
    rules = _illustrated_design_system()

    assert "AT MOST 6 label strings" in rules
    assert "copied VERBATIM from THIS slide's CONTENT" in rules
    assert (
        "Never invent a word, number, percentage,\n  date or company name for the image"
        in rules
    )
    assert "never carry\n  one over from another slide" in rules
    assert "No paragraphs, sentences or body copy inside the image" in rules


def test_label_contract_numbers_are_absent_from_non_illustration_decks():
    assert "AT MOST 6 label strings" not in _plain_design_system()


def test_slot_rules_no_longer_claim_the_image_cannot_render_text():
    # The old justification asserted a provider capability ("it cannot render
    # Korean"). That is provider-dependent and no longer true, so the rule now
    # rests on the part that still holds: the deck must not depend on the
    # picture, and a rendered picture cannot be edited afterwards.
    rules = author_slide_rules.ILLUSTRATION_SLOT_RULES

    assert "carries NO text" not in rules
    assert "cannot render Korean" not in rules
    assert "gibberish" not in rules

    assert "THE SLIDE MUST READ COMPLETELY WITH THE IMAGE MISSING" in rules
    assert "A rendered picture cannot be\n  edited afterwards" in rules
    assert "the slot is removed outright whenever image generation fails" in rules


def test_slot_rules_name_no_image_provider():
    rules = author_slide_rules.ILLUSTRATION_SLOT_RULES.lower()

    for provider in ("gpt-image", "dall-e", "dalle", "openai", "gemini", "imagen"):
        assert provider not in rules


def test_how_many_slides_get_a_picture_is_no_longer_argued_per_slide():
    """The prose that oscillated is gone; a deck-scoped plan owns the count.

    Three successive tunings of this block produced 4/77, then 73/77, then 1/77
    illustrated slides on the same real deck, because each slide answered a
    deck-level question with only its own text in view. The gates, the named
    filler-scene list and the tier classification are therefore deleted rather
    than re-tuned.
    """
    rules = _illustrated_design_system()

    for gate in ("GATE 1", "GATE 2", "GATE 3", "INTERCHANGEABILITY TEST"):
        assert gate not in rules
    for tier in ("TIER 1", "TIER 2", "TIER 3"):
        assert tier not in rules
    for deleted in (
        "MOST SLIDES IN A PROPOSAL DECK CARRY NO ILLUSTRATION",
        "The default is none",
        "People at desks",
        "wall of dashboards",
        "take the no",
    ):
        assert deleted not in rules


def test_the_slot_block_stayed_small_enough_to_read():
    # The block grew to ~68 lines across three tunings of prose that has now
    # moved out of the prompt. Keeping it short is the point of the change.
    assert len(author_slide_rules.ILLUSTRATION_SLOT_RULES.splitlines()) <= 30
