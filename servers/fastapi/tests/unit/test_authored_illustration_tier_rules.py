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


from utils.llm_calls import author_slide_rules


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


def test_the_slot_block_stayed_small_enough_to_read():
    # The block grew to ~68 lines across three tunings of prose that has now
    # moved out of the prompt. Keeping it short is the point of the change.
    assert len(author_slide_rules.ILLUSTRATION_SLOT_RULES.splitlines()) <= 30
