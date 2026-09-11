"""``AUTHORED_AUTHOR_PROMPT_PROFILE``: one knob, two whole prompts.

``current`` has to be today's prompt byte for byte -- a profile switch that
quietly changed the default would make every comparison drawn against it
meaningless -- and ``august-0819`` has to be the 2026-08-19 text *plus* every
contract the gates added since, because a profile that authored slides the
author stage then rejects would measure nothing but its own rejections.

Design: ``docs/authored-author-prompt-profiles-design-20260912.md``.
"""

import pytest

from utils.get_env_programme import (
    get_authored_author_prompt_model,
    get_authored_author_prompt_profile,
)
from utils.llm_calls import author_slide_prompt, author_slide_rules
from utils.llm_calls.author_prompt_profile import (
    AUGUST,
    CURRENT,
    AuthorPromptProfile,
    AuthorPromptProfileError,
    assemble_author_prompt,
    author_prompt_profile_scope,
    authoring_model,
    current_author_prompt_profile,
    resolve_author_prompt_profile,
)
from utils.llm_calls.author_prompt_split import split_deck_invariant
from utils.llm_calls.author_slide import Brand, build_design_system
from utils.llm_calls.author_slide_brief import (
    PRE_BUNDLE_TYPE_SCALES,
    SEMANTIC_ROLE_RULES,
    SLIDE_OUTPUT_CONTRACT,
)
from utils.llm_calls.author_slide_brief_august import (
    AUGUST_DEFAULT_STYLE_BRIEF,
    AUGUST_DESIGN_SYSTEM_RULES,
)

CONTENT = "시장 현황과 세 가지 과제"


def _brand(verbosity=None):
    brand = Brand(
        topic="2026 디지털 전환 전략",
        language="Korean",
        primary="#2563EB",
        fonts="Noto Sans KR",
    )
    if verbosity is not None:
        object.__setattr__(brand, "verbosity", verbosity)
    return brand


def _prompt(brand=None, *, role="PROBLEM", index=3, n=20):
    brand = brand or _brand()
    design_system = build_design_system(brand, None)
    return design_system, author_slide_prompt.author_prompt(
        CONTENT, design_system, brand, role, index, n
    )


@pytest.fixture(autouse=True)
def _default_profile(monkeypatch):
    """Every test states the profile it means, never the operator's setting.

    Without this an engineer running the suite with the knob exported would see
    the ``current`` assertions fail, and would have learned nothing about the
    code.
    """
    monkeypatch.delenv("AUTHORED_AUTHOR_PROMPT_PROFILE", raising=False)
    monkeypatch.delenv("AUTHORED_AUTHOR_PROMPT_MODEL", raising=False)


@pytest.fixture
def august():
    with author_prompt_profile_scope(AuthorPromptProfile(name=AUGUST)) as profile:
        yield profile


# --- `current` is the prompt it replaced -------------------------------------


def test_current_sections_are_the_live_constants():
    """Not "equal to": the same objects, so no copy can drift from its source."""
    sections = AuthorPromptProfile().sections
    assert sections.design_system_rules is author_slide_rules.DESIGN_SYSTEM_RULES
    assert sections.default_style_brief is author_slide_rules.DEFAULT_STYLE_BRIEF
    assert sections.type_scales is None
    assert sections.deck_invariant_tail is True
    assert sections.archetype_clause is True
    assert sections.design_system_last is False


@pytest.mark.parametrize("verbosity", [None, "concise", "standard", "text-heavy"])
def test_current_defers_the_type_scale_to_the_knob_that_already_decided_it(verbosity):
    profile = AuthorPromptProfile()
    assert profile.type_scale(verbosity) == author_slide_rules.type_scale_rules(verbosity)


def test_current_reassembles_the_prompt_exactly_as_it_was_written_before():
    """The pre-change assembly, recomputed here from the same parts.

    A stored snapshot of the whole prompt would fail on any later, legitimate
    edit to the brief. This instead pins the SHAPE: block order, separators and
    the slide line, which is what the profile actually changes.
    """
    brand = _brand()
    design_system = build_design_system(brand, None)
    role, index, n = "PROBLEM", 3, 20
    from utils.authored_styles import archetype_for_role

    page_marker = (
        f'PAGE MARKER FOR THIS SLIDE: render exactly "{index + 1:02d}" once, as the '
        "only footer text, using the fixed bottom-center page-number system."
    )
    expected = (
        f"{design_system}\n\n"
        f"Author slide {index + 1} of {n}. Deck topic: {brand.topic}. This slide's ROLE: "
        f"{role} ⇒ compose it with the style's \"{archetype_for_role(role)}\" archetype. "
        f"CONTENT: {CONTENT}.\n\n"
        f"{page_marker}\n\n"
        f"{SLIDE_OUTPUT_CONTRACT}"
    )
    assert author_slide_prompt.author_prompt(
        CONTENT, design_system, brand, role, index, n
    ) == expected


def test_current_keeps_the_deck_invariant_tail_and_the_hoistable_prefix():
    design_system, prompt = _prompt()
    assert prompt.startswith(design_system + "\n\n")
    head, rest = split_deck_invariant(prompt, design_system)
    assert head == design_system.strip()
    assert "Capability contract:" in design_system
    assert "Author slide 4 of 20." in rest


# --- `august-0819` is the August text ----------------------------------------


def test_august_states_the_august_brief_and_style_brief(august):
    sections = august.sections
    assert sections.design_system_rules is AUGUST_DESIGN_SYSTEM_RULES
    assert sections.default_style_brief is AUGUST_DEFAULT_STYLE_BRIEF
    _, prompt = _prompt()
    # August's own universals travelled inside the DEFAULT style brief.
    assert "- Margins ~64px (except intentional full-bleed colour zones)." in prompt
    assert "Consistent eyebrow: UPPERCASE, letter-spaced 0.15em" in prompt
    assert "Use only one uniform pixel value for border-radius" in prompt
    assert "`word-break: keep-all` AND `overflow-wrap: anywhere`" in prompt


@pytest.mark.parametrize(
    "sentence",
    [
        "Width belongs to the CONTENT BOX",
        "Connector clearance:",
        "SHARED UNIVERSALS",
        "ONE radius family",
        "archetype",
        "Capability contract:",
        "COMPOSITION INTENT",
    ],
)
def test_august_drops_the_composition_guidance_added_since(august, sentence):
    _, prompt = _prompt()
    assert sentence not in prompt


def test_august_states_the_design_system_last(august):
    design_system, prompt = _prompt()
    assert prompt.startswith("Author slide 4 of 20.")
    assert prompt.endswith(f"{design_system}\n\n{SLIDE_OUTPUT_CONTRACT}")
    # The system-message hoist needs the design system FIRST, so under this
    # profile it degrades to sending the whole prompt in the user message
    # rather than losing text.
    assert split_deck_invariant(prompt, design_system) == ("", prompt)


def test_august_names_the_role_without_an_archetype(august):
    _, prompt = _prompt()
    assert "This slide's ROLE: PROBLEM. CONTENT:" in prompt


def test_august_drops_the_deck_invariant_tail(august):
    design_system, _ = _prompt()
    assert design_system.endswith("Return ONLY the complete HTML document.")


@pytest.mark.parametrize("verbosity", ["concise", "standard", "text-heavy"])
def test_august_pins_the_pre_bundle_type_scale_over_the_bundle_knob(
    august, monkeypatch, verbosity
):
    monkeypatch.setenv("AUTHORED_PROMPT_BUNDLE_ENABLED", "true")
    assert august.type_scale(verbosity) == PRE_BUNDLE_TYPE_SCALES[verbosity]
    _, prompt = _prompt(_brand(verbosity))
    assert PRE_BUNDLE_TYPE_SCALES[verbosity] in prompt
    assert "this is a ramp, not a floor" not in prompt


# --- the contracts that hold under every profile -----------------------------


@pytest.mark.parametrize(
    "marker",
    [
        "data-qid",
        'data-semantic-role="title"',
        "SEMANTIC ROLES (the repair pass and the capacity gate read these marks)",
        "-webkit-line-clamp",
        "are all forbidden and are rejected before rendering",
        "SELF-CONTAINED FONT POLICY",
        "Document head: emit at most",
        "INLINE SVG CONTRACT",
        "PAGE MARKER FOR THIS SLIDE",
        "Return ONLY the complete HTML document.",
    ],
)
def test_every_profile_states_every_required_contract(marker):
    for name in (CURRENT, AUGUST):
        with author_prompt_profile_scope(AuthorPromptProfile(name=name)):
            _, prompt = _prompt()
        assert marker in prompt, f"{marker!r} missing under {name}"


def test_the_two_contract_insertions_are_in_the_august_brief():
    assert SEMANTIC_ROLE_RULES in AUGUST_DESIGN_SYSTEM_RULES.format(
        primary="#2563EB",
        fonts="Noto Sans KR",
        language="Korean",
        style_brief="",
        semantic_roles=SEMANTIC_ROLE_RULES,
        type_scale="",
        header_system="",
        page_number_system="",
    )
    assert "{semantic_roles}" in AUGUST_DESIGN_SYSTEM_RULES
    assert "NEVER hide overflowing content" in AUGUST_DESIGN_SYSTEM_RULES


def test_the_requirement_and_chapter_markers_are_profile_invariant(august):
    """Header-contract text is a contract with the header repair pass, not taste."""
    _, prompt = _prompt(_brand())
    with_marker = author_slide_prompt.author_prompt(
        CONTENT,
        build_design_system(_brand(), None),
        _brand(),
        "PROBLEM",
        3,
        20,
        requirements_enabled=True,
        chapter_breadcrumb=True,
        chapter_prefix="2.",
    )
    assert 'data-requirement-ids="ID1,ID2"' in with_marker
    assert "HEADER EYEBROW PREFIX FOR THIS SLIDE" in with_marker
    assert "data-requirement-ids" not in prompt


# --- resolution, pinning and the model pin -----------------------------------


def test_the_knob_resolves_to_a_profile(monkeypatch):
    monkeypatch.delenv("AUTHORED_AUTHOR_PROMPT_PROFILE", raising=False)
    assert resolve_author_prompt_profile().name == CURRENT
    monkeypatch.setenv("AUTHORED_AUTHOR_PROMPT_PROFILE", AUGUST)
    assert get_authored_author_prompt_profile() == AUGUST
    assert resolve_author_prompt_profile().name == AUGUST
    assert current_author_prompt_profile().name == AUGUST


@pytest.mark.parametrize("value", ["August-0819", " august-0819", "august", "0819", "off"])
def test_a_value_outside_the_two_choices_is_refused_not_rounded(monkeypatch, value):
    monkeypatch.setenv("AUTHORED_AUTHOR_PROMPT_PROFILE", value)
    with pytest.raises(ValueError):
        resolve_author_prompt_profile()


def test_an_unknown_profile_name_is_a_coded_rejection():
    with pytest.raises(AuthorPromptProfileError):
        AuthorPromptProfile(name="september")
    with pytest.raises(AuthorPromptProfileError):
        AuthorPromptProfile(model="   ")


def test_a_pinned_profile_outranks_the_ambient_environment(monkeypatch):
    monkeypatch.setenv("AUTHORED_AUTHOR_PROMPT_PROFILE", AUGUST)
    with author_prompt_profile_scope(AuthorPromptProfile(name=CURRENT)):
        assert current_author_prompt_profile().name == CURRENT
        _, prompt = _prompt()
        assert "archetype" in prompt
    assert current_author_prompt_profile().name == AUGUST


def test_the_model_pin_is_unset_by_default_and_pins_when_set(monkeypatch):
    monkeypatch.delenv("AUTHORED_AUTHOR_PROMPT_MODEL", raising=False)
    assert get_authored_author_prompt_model() is None
    assert resolve_author_prompt_profile().model is None
    assert authoring_model("provider-default") == "provider-default"
    monkeypatch.setenv("AUTHORED_AUTHOR_PROMPT_MODEL", "  gpt-5.6-sol  ")
    assert get_authored_author_prompt_model() == "gpt-5.6-sol"
    with author_prompt_profile_scope(resolve_author_prompt_profile()):
        assert authoring_model("provider-default") == "gpt-5.6-sol"


def test_the_model_pin_does_not_change_a_single_character_of_the_prompt(monkeypatch):
    _, plain = _prompt()
    with author_prompt_profile_scope(AuthorPromptProfile(model="some-model")):
        _, pinned = _prompt()
    assert pinned == plain


# --- the assembler -----------------------------------------------------------


def test_an_absent_block_contributes_nothing_under_either_profile():
    blocks = {"chapter": "", "requirement": "R", "anchor": None}
    for name, expected in (
        (CURRENT, "DS\n\nR\n\nLINE\n\nPAGE\n\nEND"),
        (AUGUST, "LINE\n\nPAGE\n\nR\n\nDS\n\nEND"),
    ):
        assert (
            assemble_author_prompt(
                design_system="DS",
                blocks=blocks,
                slide_line="LINE",
                page_marker="PAGE",
                closing="END",
                profile=AuthorPromptProfile(name=name),
            )
            == expected
        )


# --- the numbers the design document quotes ----------------------------------


def test_the_recorded_character_counts_still_hold():
    """The report's table is measured, so it fails here if it stops being true."""
    design_system, prompt = _prompt()
    with author_prompt_profile_scope(AuthorPromptProfile(name=AUGUST)):
        august_design_system, august_prompt = _prompt()
    assert (len(design_system), len(prompt)) == (11569, 12194)
    assert (len(august_design_system), len(august_prompt)) == (9335, 9910)
    assert len(prompt) - len(august_prompt) == 2284
