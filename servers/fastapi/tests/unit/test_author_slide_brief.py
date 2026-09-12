"""The 2026-09-05 authoring-brief changes (design sections G1-G8, G10, G11).

Each test here pins a decision the 2026-09-05 prompt diagnosis made, not the
wording that carries it: the ORDER the prompt is assembled in, WHICH blocks are
paid for on a given deck, and that a number stated twice comes from one source.
"""

import difflib
from types import SimpleNamespace

import pytest

from services import authored_deck_plans
from services.authored_vnext.capability_registry import (
    CAPABILITY_REGISTRY_DIGEST,
    prompt_capability_clause,
)
from utils.authored_infographic.contracts import content_bottom_px
from utils.authored_style_exemplars import (
    AUTHORED_STYLE_EXEMPLARS_DIRECTORY,
    MAX_EXEMPLAR_CHARS,
    exemplar_block,
    load_style_exemplar,
)
from utils.authored_styles import archetype_for_role, resolve_authored_style
from utils.llm_calls import author_slide_prompt
from utils.llm_calls.author_deck_tokens import deck_tokens, deck_tokens_block
from utils.llm_calls.author_slide import Brand, build_design_system
from utils.llm_calls.author_slide_brief import (
    CONTENT_FONT_FLOOR_PX,
    PRE_BUNDLE_TYPE_SCALES,
    SEMANTIC_ROLE_RULES,
    SHARED_UNIVERSALS,
    composition_intent_block,
    type_ramp,
    type_scale_text,
)
from utils.llm_calls.author_slide_prompt import author_prompt
from utils.llm_calls.author_slide_rules import DEFAULT_STYLE_BRIEF, type_scale_rules
from utils.renderer_isolation import admit_authored_html

#: The composition-shaping half of the 2026-09-05 bundle is switchable and OFF
#: by default, so every test that reads it states which state it is testing.
BUNDLE_KNOB = "AUTHORED_PROMPT_BUNDLE_ENABLED"


@pytest.fixture(autouse=True)
def _bundle_off_unless_a_test_says_otherwise(monkeypatch):
    """No test inherits an operator's knob from the environment."""
    monkeypatch.delenv(BUNDLE_KNOB, raising=False)


def _bundle(monkeypatch, enabled: bool) -> None:
    monkeypatch.setenv(BUNDLE_KNOB, "true" if enabled else "false")


class _FakeAnchor:
    """The duck-typed object `author_style_anchor` supplies (interface (i))."""

    def prompt_block(self) -> str:
        return "DECK STYLE ANCHOR: slide 2 of this deck is already authored."


def _brand(**overrides) -> Brand:
    return Brand(topic="주제", **overrides)


def _style(**overrides) -> SimpleNamespace:
    base = dict(id="editorial", brief="- Editorial")
    base.update(overrides)
    return SimpleNamespace(**base)


# --- G8: the order the prompt is assembled in --------------------------------


def test_the_deck_invariant_design_system_leads_and_the_slide_follows():
    prompt = author_prompt("CONTENT-BODY", "DESIGN-SYSTEM", _brand(), "PILLARS", 2, 9)

    positions = [
        prompt.index("DESIGN-SYSTEM"),
        prompt.index("Author slide 3 of 9"),
        prompt.index("CONTENT: CONTENT-BODY"),
        prompt.index("PAGE MARKER FOR THIS SLIDE"),
        prompt.index("Output ONLY the complete self-contained HTML document."),
    ]
    assert positions == sorted(positions)


def test_the_anchor_sits_after_the_design_system_and_before_the_user_direction():
    prompt = author_prompt(
        "CONTENT",
        "DESIGN-SYSTEM",
        _brand(),
        "PILLARS",
        2,
        9,
        instructions="표를 많이 넣어줘",
        style_anchor=_FakeAnchor(),
    )

    assert (
        prompt.index("DESIGN-SYSTEM")
        < prompt.index("DECK STYLE ANCHOR")
        < prompt.index("THE USER'S OWN REQUEST")
        < prompt.index("CONTENT:")
    )


def test_no_anchor_leaves_the_prompt_exactly_as_an_unanchored_deck_reads_it():
    baseline = author_prompt("CONTENT", "DESIGN", _brand(), "PILLARS", 2, 9)

    assert baseline == author_prompt(
        "CONTENT", "DESIGN", _brand(), "PILLARS", 2, 9, style_anchor=None
    )
    assert "DECK STYLE ANCHOR" not in baseline


# --- G7: the ROLE -> archetype bridge ----------------------------------------


def test_every_role_is_bridged_to_an_archetype_the_style_brief_names():
    assert archetype_for_role("COVER") == "cover"
    assert archetype_for_role("ROADMAP") == "timeline"
    assert archetype_for_role("OUTCOMES") == "data"
    # The whole point of the bridge: the roles a style brief never names by
    # their role word still land on an archetype it does describe.
    assert archetype_for_role("PILLARS") == "content"
    assert archetype_for_role("") == "content"

    prompt = author_prompt("CONTENT", "DESIGN", _brand(), "ROADMAP", 2, 9)
    assert 'ROLE: ROADMAP ⇒ compose it with the style\'s "timeline" archetype' in prompt


# --- G3: what actually has to carry a data-qid -------------------------------


def test_the_qid_contract_covers_text_owners_rather_than_every_shape():
    prompt = author_prompt("CONTENT", "DESIGN", _brand(), "PILLARS", 2, 9)

    assert "every element that owns text" in prompt
    # ~500 output tokens a slide were spent naming decorative shapes and
    # wrappers whose declared qids the server never validated.
    assert "simple shape" not in prompt
    assert "meaningful grouping element" not in prompt


# --- G1: composition intent, and the knob that switches it off ----------------


def test_the_composition_intent_states_the_bottom_line_from_one_source(monkeypatch):
    _bundle(monkeypatch, True)
    block = composition_intent_block()

    assert f"y={content_bottom_px('template')}px" in block
    assert f"y={content_bottom_px('none')}px" in block
    assert "ONE element dominant per slide" in block
    assert '<table data-native="table">' in block
    assert "labelled HTML/CSS bar or column chart" in block


def test_the_shaping_half_of_the_bundle_is_off_unless_an_operator_asks():
    """The blind paired rating went 13 parent / 7 bundle / 4 tie over 24 slides.

    So the default deck reads the parent's pre-bundle type scale and carries no
    COMPOSITION INTENT block at all.
    """
    assert composition_intent_block() == ""

    design_system = build_design_system(_brand())
    assert "COMPOSITION INTENT" not in design_system
    assert PRE_BUNDLE_TYPE_SCALES["standard"] in design_system
    assert "a ramp, not a floor" not in design_system


def test_the_mechanical_half_of_the_bundle_ships_in_either_state(monkeypatch):
    """These are corrections, not taste, so the knob must not reach them.

    The semantic-role vocabulary feeds the role repair and the capacity gate,
    the universals were rescued from a style brief 37 of 38 presets replace,
    and the ROLE bridge is what makes a style's archetypes reachable.
    """
    for enabled in (False, True):
        _bundle(monkeypatch, enabled)
        design_system = build_design_system(_brand(), _style())

        assert SEMANTIC_ROLE_RULES in design_system
        assert 'exactly ONE element per slide `data-semantic-role="title"`' in design_system
        assert "SHARED UNIVERSALS" in design_system
        assert "ONE accent hue per slide" in design_system
        assert "INLINE SVG CONTRACT" in design_system
        # G5 contradiction fixes.
        assert "ONE radius family for the whole deck" in design_system
        assert "`word-break: keep-all`" in design_system
        assert "those are fixed furniture and are meant to be pinned" in design_system
        # G10 deck tokens.
        assert "any other hue is an error" in design_system

        prompt = author_prompt("CONTENT", design_system, _brand(), "ROADMAP", 2, 9)
        assert 'ROLE: ROADMAP ⇒ compose it with the style\'s "timeline" archetype' in prompt


def test_the_knob_moves_exactly_the_intent_block_and_the_type_scale(monkeypatch):
    """Nothing else in the design system may depend on the knob."""
    _bundle(monkeypatch, False)
    off = build_design_system(_brand()).splitlines(keepends=True)
    _bundle(monkeypatch, True)
    on = build_design_system(_brand()).splitlines(keepends=True)

    delta = _delta(off, on)
    added = [line[2:] for line in delta if line.startswith("+ ")]
    removed = [line[2:] for line in delta if line.startswith("- ")]

    _bundle(monkeypatch, True)
    assert "".join(added) == (
        composition_intent_block() + "\n" + type_scale_text("standard") + "\n"
    )
    _bundle(monkeypatch, False)
    assert "".join(removed) == PRE_BUNDLE_TYPE_SCALES["standard"] + "\n"


def _delta(off: list[str], on: list[str]) -> list[str]:
    """The ndiff lines that actually changed, hint lines ('? ...') dropped."""
    return [
        line for line in difflib.ndiff(off, on) if line.startswith(("+ ", "- "))
    ]


def test_the_semantic_role_vocabulary_survives_the_knob_being_off():
    """Bench run 26: nothing ever told the model what data-semantic-role means.

    The gate requires exactly one title text owner per slide and blocks the
    whole deck without it, so the vocabulary is stated whatever the knob says —
    which is why it was lifted out of ``composition_intent_block``.
    """
    block = SEMANTIC_ROLE_RULES

    assert "data-semantic-role" in block
    assert "title, body, supporting" in block
    assert 'exactly ONE element per slide `data-semantic-role="title"`' in block
    assert "never `decoration`" in block
    assert block in build_design_system(_brand())


def test_the_semantic_role_vocabulary_distinguishes_body_from_supporting() -> None:
    """Bench run 30, pass B: the model filed 14 owners as `supporting` and
    none as `body`, so the gate blocked the whole deck on a missing body
    owner even though a title was correctly marked.
    """
    block = SEMANTIC_ROLE_RULES

    assert "`body` is the slide's readable content" in block
    assert "`supporting` is short structural text only" in block
    assert "A content slide must have body owners." in block


@pytest.mark.parametrize("bundle_enabled", [False, True])
def test_every_role_including_the_cover_is_told_how_to_mark_its_title(
    monkeypatch, bundle_enabled: bool
):
    """One prompt builder serves every lane, so every role gets one sentence.

    The cover is excluded from the header contract (``DEFAULT_EXCLUDED_ROLES``),
    which used to leave it with no structural title marker at all.
    """
    _bundle(monkeypatch, bundle_enabled)
    design_system = build_design_system(_brand())
    for role in ("COVER", "AGENDA", "PILLARS", "SECTION", "CLOSING"):
        prompt = author_prompt("CONTENT", design_system, _brand(), role, 0, 9)
        assert 'exactly ONE element per slide `data-semantic-role="title"`' in prompt
        assert "on a cover the main title" in prompt


def test_the_lane_knobs_do_not_change_what_the_cover_is_told(monkeypatch):
    """Both P0 lanes share one prompt builder; the knobs only schedule calls.

    Bench runs 24-26 correlated the mislabelled cover with the legacy lane, so
    this pins that the lane knobs cannot introduce a role-guidance difference.
    """
    prompts = []
    for barrier, streaming in (("true", "false"), ("false", "true")):
        monkeypatch.setenv("AUTHORED_WAVE_BARRIER_DISABLED", barrier)
        monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", streaming)
        prompts.append(
            author_prompt(
                "CONTENT", build_design_system(_brand()), _brand(), "COVER", 0, 9
            )
        )
    assert prompts[0] == prompts[1]
    assert 'data-semantic-role="title"' in prompts[0]


def test_the_composition_intent_reaches_every_deck_including_a_preset_one(monkeypatch):
    _bundle(monkeypatch, True)
    for design_system in (
        build_design_system(_brand()),
        build_design_system(_brand(), _style()),
    ):
        assert "COMPOSITION INTENT" in design_system


# --- G2: the type ramp --------------------------------------------------------


@pytest.mark.parametrize("verbosity", ["concise", "standard", "text-heavy"])
def test_every_verbosity_states_a_ramp_and_the_same_single_floor(
    monkeypatch, verbosity: str
):
    _bundle(monkeypatch, True)
    text = type_scale_rules(verbosity)
    ramp = type_ramp(verbosity)

    assert text == type_scale_text(verbosity)
    assert "a ramp, not a floor" in text
    assert f"body copy and bullets {ramp.body}px" in text
    # The tier the 2026-09-05 census found missing entirely.
    assert "claim or sub-heading" in text
    assert f"below {CONTENT_FONT_FLOOR_PX}px" in text
    # The three scales previously stated three different floors (15/13/12).
    for stale_floor in ("below 15px", "below 13px"):
        assert stale_floor not in text


@pytest.mark.parametrize("verbosity", ["concise", "standard", "text-heavy"])
def test_the_knob_off_restores_the_parent_floor_wording_verbatim(verbosity: str):
    """The pre-bundle text is a parent baseline, not a paraphrase of it."""
    text = type_scale_rules(verbosity)

    assert text == PRE_BUNDLE_TYPE_SCALES[verbosity]
    assert text != type_scale_text(verbosity)
    assert "a ramp, not a floor" not in text
    assert "claim or sub-heading" not in text


def test_a_concise_deck_writes_larger_than_a_text_heavy_one():
    assert type_ramp("concise").body > type_ramp("standard").body
    assert type_ramp("standard").body > type_ramp("text-heavy").body
    assert type_ramp("text-heavy").footnote >= CONTENT_FONT_FLOOR_PX
    assert type_ramp("nonsense") == type_ramp("standard")


# --- G6: universals belong to every deck, not to the default style ------------


def test_the_universals_moved_out_of_the_default_style_brief():
    for universal in ("Margins ~64px", "ONE accent hue per slide", "Eyebrow:"):
        assert universal in SHARED_UNIVERSALS
        assert universal not in DEFAULT_STYLE_BRIEF

    # 37 of 38 presets REPLACE the default brief, so a preset deck used to lose
    # every one of these.
    preset_system = build_design_system(_brand(), _style())
    assert "SHARED UNIVERSALS" in preset_system
    for universal in ("Margins ~64px", "ONE accent hue per slide"):
        assert universal in preset_system


# --- G4: the illustration slot block is paid for only when it can be used -----


def _illustration_style() -> SimpleNamespace:
    return _style(id="iso", brief="- Iso", illustration_prompt="isometric style")


def test_the_slot_rules_are_dropped_when_the_deck_illustrates_no_slide(monkeypatch):
    monkeypatch.setattr(
        author_slide_prompt, "get_authored_illustration_share", lambda: 0.0
    )
    assert "ILLUSTRATION SLOT" not in build_design_system(
        _brand(), _illustration_style()
    )

    monkeypatch.setattr(
        author_slide_prompt, "get_authored_illustration_share", lambda: 0.25
    )
    assert "ILLUSTRATION SLOT" in build_design_system(_brand(), _illustration_style())


def test_the_capability_clause_no_longer_carries_the_registry_digest():
    assert CAPABILITY_REGISTRY_DIGEST not in prompt_capability_clause()
    assert "Capability contract" in prompt_capability_clause()


# --- G4: the deterministic illustration share --------------------------------


_TEN_SLIDE_ROLES = [
    "COVER", "AGENDA", "PROBLEM", "PILLARS", "SECTION",
    "OUTCOMES", "ROADMAP", "ARCHITECTURE", "METRICS", "CLOSING",
]
#: The six content positions of the deck above, in order.
_CONTENT_INDICES = [2, 3, 5, 6, 7, 8]


def _slots(monkeypatch, share: float) -> tuple[bool, ...]:
    monkeypatch.setattr(
        authored_deck_plans, "get_authored_illustration_share", lambda: share
    )
    plan = authored_deck_plans.illustration_share_plan(_TEN_SLIDE_ROLES)
    return tuple(plan.allows(index) for index in range(len(_TEN_SLIDE_ROLES)))


def test_a_quarter_share_picks_every_fourth_content_slide_after_the_anchor(monkeypatch):
    slots = _slots(monkeypatch, 0.25)

    # Content positions 1 and 5 of [2, 3, 5, 6, 7, 8] -> slide indices 3 and 8.
    assert [index for index, allowed in enumerate(slots) if allowed] == [3, 8]
    # The first content slide is the deck's style anchor and stays picture-free.
    assert slots[_CONTENT_INDICES[0]] is False


def test_a_zero_share_is_a_decision_that_no_slide_carries_a_picture(monkeypatch):
    slots = _slots(monkeypatch, 0.0)

    assert slots == (False,) * len(_TEN_SLIDE_ROLES)
    # An empty plan is a decision; None would restore the per-slide judgement.
    assert authored_deck_plans.illustration_share_plan(_TEN_SLIDE_ROLES) is not None


def test_a_full_share_still_spares_the_anchor_and_the_non_content_roles(monkeypatch):
    slots = _slots(monkeypatch, 1.0)

    assert [index for index, allowed in enumerate(slots) if allowed] == (
        _CONTENT_INDICES[1:]
    )
    for index, role in enumerate(_TEN_SLIDE_ROLES):
        if role in {"COVER", "SECTION", "AGENDA", "CLOSING"}:
            assert slots[index] is False


# --- G10: the deck's literal tokens ------------------------------------------


def test_the_token_block_states_this_style_s_own_palette_literally():
    style = resolve_authored_style("hyundai-culture-center")
    tokens = deck_tokens(style, _brand())

    assert tokens["accent"] == "#08916E"
    assert tokens["surface"] == "#F9F9F9"
    # The palette's next signal colour, not a repeat of the accent.
    assert tokens["accent-2"] == "#219F88"
    assert tokens["ink"] not in {tokens["accent"], tokens["surface"]}

    block = deck_tokens_block(style, _brand())
    assert "any other hue is an error" in block
    # G10 explicitly stays inside the current CSS admission policy.
    assert "--" not in block
    for name in ("ink", "accent-2", "radius", "gutter", "pad", "t-body", "t-label"):
        assert f"{name} " in block


def test_the_type_tokens_are_the_type_ramp_rather_than_a_second_statement():
    for verbosity in ("concise", "standard", "text-heavy"):
        tokens = deck_tokens(None, _brand(verbosity=verbosity))
        ramp = type_ramp(verbosity)
        assert tokens["t-body"] == f"{ramp.body}px"
        assert tokens["t-display"] == f"{ramp.display}px"
        assert tokens["t-label"] == f"{ramp.label}px"


def test_a_deck_with_no_style_still_gets_a_complete_token_set():
    tokens = deck_tokens(None, _brand())

    assert tokens["accent"] == "#2563EB"
    assert set(tokens) == {
        "ink", "accent", "accent-2", "surface", "hairline", "radius", "gutter",
        "pad", "t-display", "t-h2", "t-body", "t-label",
    }


# --- G11: the exemplar --------------------------------------------------------


def test_the_checked_in_exemplars_are_renderable_and_within_the_cap():
    files = sorted(AUTHORED_STYLE_EXEMPLARS_DIRECTORY.glob("*.html"))
    assert files, "the programme ships at least one exemplar"
    for path in files:
        html = path.read_text(encoding="utf-8")
        assert len(html) <= MAX_EXEMPLAR_CHARS
        # An exemplar that could not be rendered is not an example of anything.
        admit_authored_html(html)
        # Image bytes teach nothing and would cost megabytes on every call.
        assert "data:image" not in html


def test_the_exemplar_reaches_the_design_system_of_the_style_that_has_one():
    style = resolve_authored_style("hyundai-culture-center")
    system = build_design_system(_brand(), style)

    assert "EXEMPLAR (one authored reference slide for this style" in system
    assert "never its text or layout" in system
    assert load_style_exemplar("hyundai-culture-center") in system


def test_a_style_without_an_exemplar_and_a_disabled_knob_add_nothing(monkeypatch):
    assert load_style_exemplar("no-such-style-id") is None
    assert exemplar_block(None) == ""
    # Traversal can never leave the exemplars directory.
    assert load_style_exemplar("../../../etc/passwd") is None

    monkeypatch.setattr(
        "utils.authored_style_exemplars.is_authored_exemplar_enabled", lambda: False
    )
    assert load_style_exemplar("hyundai-culture-center") is None
    assert "EXEMPLAR (" not in build_design_system(
        _brand(), resolve_authored_style("hyundai-culture-center")
    )
