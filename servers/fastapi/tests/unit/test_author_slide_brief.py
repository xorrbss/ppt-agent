"""The 2026-09-05 authoring-brief changes (design sections G1-G8, G10, G11).

Each test here pins a decision the 2026-09-05 prompt diagnosis made, not the
wording that carries it: the ORDER the prompt is assembled in, WHICH blocks are
paid for on a given deck, and that a number stated twice comes from one source.
"""

import difflib
from types import SimpleNamespace

import pytest

from services.ppt_writing_policy import (
    WRITING_POLICY_ID,
    ProtectedSpan,
    scan_visible_text,
)

from models.authored_vnext.capability_registry import (
    CAPABILITY_REGISTRY_DIGEST,
    prompt_capability_clause,
)
from utils.authored_infographic.contracts import content_bottom_px
from utils.authored_style_exemplars import (
    AUTHORED_STYLE_EXEMPLARS_DIRECTORY,
    MAX_EXEMPLAR_CHARS,
)
from utils.llm_calls.author_slide_brief import (
    CONTENT_FONT_FLOOR_PX,
    PRE_BUNDLE_TYPE_SCALES,
    SEMANTIC_ROLE_RULES,
    composition_intent_block,
    type_ramp,
    type_scale_text,
)
from utils.llm_calls.author_slide_rules import type_scale_rules
from utils.renderer_isolation import admit_authored_html

#: The composition-shaping half of the 2026-09-05 bundle is switchable and OFF
#: by default, so every test that reads it states which state it is testing.
BUNDLE_KNOB = "AUTHORED_PROMPT_BUNDLE_ENABLED"


def test_writing_policy_is_retained_in_durable_request_without_legacy_default():
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.presentation_generation_staging import _durable_request_payload

    legacy = GeneratePresentationRequest(content="문구", strategy="authored")
    assert legacy.writing_policy is None
    assert "writing_policy" not in _durable_request_payload(legacy)
    current = legacy.model_copy(update={"writing_policy": WRITING_POLICY_ID})
    payload = _durable_request_payload(current)
    restored = GeneratePresentationRequest.model_validate(payload)
    assert restored.writing_policy == WRITING_POLICY_ID
    assert payload["writing_policy"] == WRITING_POLICY_ID


def test_writing_policy_changes_brief_cache_key_without_changing_legacy_input():
    from services.authored_content_brief_checkpoint import content_brief_checkpoint_input

    kwargs = dict(content="승인 후 적용", language="ko", additional_context=None,
                  tone=None, verbosity=None, instructions=None)
    legacy = content_brief_checkpoint_input(**kwargs)
    current = content_brief_checkpoint_input(**kwargs, writing_policy=WRITING_POLICY_ID)
    assert "writingPolicy" not in legacy
    assert current == {**legacy, "writingPolicy": WRITING_POLICY_ID}


def test_writing_policy_preserves_source_symbols_but_flags_new_copy():
    source = "A·B 제품"
    text = source + " 중복·누락 — 개선"
    result = scan_visible_text(text, protected_spans=(ProtectedSpan(0, len(source), "source-name-1"),))
    assert result.protected_counts == {"middle_dot": 1}
    assert result.violation_counts == {"middle_dot": 1, "long_dash": 1}
    assert text == source + " 중복·누락 — 개선"
    with pytest.raises(ValueError):
        scan_visible_text(text, protected_spans=(ProtectedSpan(0, len(text), "generated-copy"),))


def test_writing_policy_keeps_numeric_ranges_urls_and_advisory_style_separate():
    result = scan_visible_text("핵심은 10–20%와 3/4, 5 N·m, 3⋅4, https://example.com/a/b를 확인하는 것입니다.")
    assert not result.violations
    assert result.protected_counts["long_dash"] == 1
    assert result.protected_counts["middle_dot"] == 2
    assert result.style_signals


def test_writing_policy_checks_display_nodes_with_literal_source_provenance():
    from services.ppt_writing_visible import (
        assess_visible_nodes, literal_source_values, svg_text_nodes,
    )

    svg = ('<svg xmlns="http://www.w3.org/2000/svg"><metadata>숨김·문구</metadata>'
           '<text>A·B 제품</text><text>중복<tspan>·누락</tspan> — 개선</text></svg>')
    values = literal_source_values(bindings=({"kind": "text", "text": "A·B 제품"},))
    report = assess_visible_nodes(svg_text_nodes(svg), source_values=values)
    assert report["visible_node_count"] == 2
    assert report["protected_symbol_counts"] == {"middle_dot": 1}
    assert report["hard_violation_counts"] == {"middle_dot": 1, "long_dash": 1}
    assert "A·B 제품" not in str(report)


def test_writing_policy_html_keeps_inline_numeric_semantics_and_skips_hidden_metadata():
    from services.ppt_writing_visible import assess_visible_nodes, html_text_nodes

    html = ('<html><head><title>숨김·메타</title></head><body>'
            '<p>범위 10<span>–</span>20%와 <b>중복·누락</b></p>'
            '<p hidden>숨김·문구</p></body></html>')
    report = assess_visible_nodes(html_text_nodes(html))
    assert report["hard_violation_counts"] == {"middle_dot": 1}
    assert report["protected_symbol_counts"] == {"long_dash": 1}


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


def _style(**overrides) -> SimpleNamespace:
    base = dict(id="editorial", brief="- Editorial")
    base.update(overrides)
    return SimpleNamespace(**base)


# --- G8: the order the prompt is assembled in --------------------------------


# --- G7: the ROLE -> archetype bridge ----------------------------------------


# --- G3: what actually has to carry a data-qid -------------------------------


# --- G1: composition intent, and the knob that switches it off ----------------


def test_the_composition_intent_states_the_bottom_line_from_one_source(monkeypatch):
    _bundle(monkeypatch, True)
    block = composition_intent_block()

    assert f"y={content_bottom_px('template')}px" in block
    assert f"y={content_bottom_px('none')}px" in block
    assert "ONE element dominant per slide" in block
    assert '<table data-native="table">' in block
    assert "labelled HTML/CSS bar or column chart" in block


def _delta(off: list[str], on: list[str]) -> list[str]:
    """The ndiff lines that actually changed, hint lines ('? ...') dropped."""
    return [
        line for line in difflib.ndiff(off, on) if line.startswith(("+ ", "- "))
    ]


def test_the_semantic_role_vocabulary_distinguishes_body_from_supporting() -> None:
    """Bench run 30, pass B: the model filed 14 owners as `supporting` and
    none as `body`, so the gate blocked the whole deck on a missing body
    owner even though a title was correctly marked.
    """
    block = SEMANTIC_ROLE_RULES

    assert "`body` is the slide's readable content" in block
    assert "`supporting` is short structural text only" in block
    assert "A content slide must have body owners." in block


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


# --- G4: the illustration slot block is paid for only when it can be used -----


def _illustration_style() -> SimpleNamespace:
    return _style(id="iso", brief="- Iso", illustration_prompt="isometric style")


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


# --- G10: the deck's literal tokens ------------------------------------------


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


