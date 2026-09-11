"""The single envelope-profile switch (``current`` vs ``august``).

``current`` is the default and must state today's behaviour exactly: every
assertion in the first section is the behaviour that existed before the switch.
``august`` is the pre-2026-08-28 contract for ORDINARY decks only -- no
server-frozen envelope, no blocking DOM quality gate, no deck-wide figure plan
-- and must not reach the source-fidelity profiles, whose envelope is a
fidelity contract rather than a density preference.

Design: ``docs/authored-composition-envelope-profiles-design-20260912.md``.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from services.authored_composition_envelope_profile import (
    AUGUST_ENVELOPE_PROFILE,
    CURRENT_ENVELOPE_PROFILE,
    DEFAULT_ROLE_BUDGET,
    ordinary_envelope_enforced,
    ordinary_infographic_plan_enabled,
    resolve_envelope_profile,
    role_budget,
)
from services.authored_composition_policy import (
    CompositionProfile,
    aggregate_owner_capacity_violations,
    build_authored_capacity_plan,
    capacity_violations,
    resolve_composition_profile,
)
from services.authored_deck_plans import _infographic_plan_applies
from utils.get_env_programme import get_authored_composition_envelope_profile
from utils.llm_calls.author_slide import STYLE_REFERENCE

_PROFILE_KNOB = "AUTHORED_COMPOSITION_ENVELOPE_PROFILE"
_KNOBS = (
    _PROFILE_KNOB,
    "AUTHORED_INFOGRAPHIC_PLAN_ENABLED",
    "AUTHORED_COMPOSITION_ENVELOPE_ENABLED",
)
_ORDINARY = CompositionProfile("ordinary", "authored", True, True, 1)
_VERBATIM = CompositionProfile("source-verbatim", "verbatim", False, True, 2)
_DENSE_HTML = "<section>" + "".join(
    f'<div data-qid="body.item{i}" style="font-size:12px">항목 {i} 설명 문장</div>'
    for i in range(80)
) + "</section>"


@pytest.fixture(autouse=True)
def _clean_knobs(monkeypatch):
    for key in _KNOBS:
        monkeypatch.delenv(key, raising=False)


def _style() -> SimpleNamespace:
    return SimpleNamespace(reference_mode=STYLE_REFERENCE)


def _reflow_request() -> SimpleNamespace:
    return SimpleNamespace(
        _source_content_authored_reflow=True,
        content_policy=SimpleNamespace(text="verbatim"),
    )


# --------------------------------------------------------------------------
# current: the default states today's behaviour and decides nothing new.
# --------------------------------------------------------------------------


def test_the_default_profile_is_current() -> None:
    assert get_authored_composition_envelope_profile() == "current"
    assert resolve_envelope_profile() is CURRENT_ENVELOPE_PROFILE


def test_an_unset_profile_resolves_to_current(monkeypatch) -> None:
    monkeypatch.setenv(_PROFILE_KNOB, "")

    assert resolve_envelope_profile() is CURRENT_ENVELOPE_PROFILE


def test_current_defers_every_per_rule_knob_it_replaces(monkeypatch) -> None:
    # ``None`` means "state nothing"; the pre-existing knob still decides.
    assert CURRENT_ENVELOPE_PROFILE.ordinary_envelope_enforced is None
    assert CURRENT_ENVELOPE_PROFILE.ordinary_infographic_plan is None

    assert ordinary_envelope_enforced(CURRENT_ENVELOPE_PROFILE) is False
    assert ordinary_infographic_plan_enabled(CURRENT_ENVELOPE_PROFILE) is False

    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_INFOGRAPHIC_PLAN_ENABLED", "true")

    assert ordinary_envelope_enforced(CURRENT_ENVELOPE_PROFILE) is True
    assert ordinary_infographic_plan_enabled(CURRENT_ENVELOPE_PROFILE) is True


def test_current_keeps_the_ordinary_deck_free_and_gated() -> None:
    slide = build_authored_capacity_plan(("a",), ("PROBLEM",), _ORDINARY).at(0)

    assert slide.enforced is False
    assert slide.min_font_px == 12
    assert slide.prompt_brief(_ORDINARY) == ""
    assert resolve_composition_profile(SimpleNamespace()).strict_dom_gate is True


def test_current_keeps_the_bound_envelope_reachable(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    slide = build_authored_capacity_plan(("a",), ("PROBLEM",), _ORDINARY).at(0)

    assert slide.enforced is True
    assert slide.min_font_px == 15
    assert (slide.max_visible_text_chars, slide.max_qids) == (460, 40)


def test_current_keeps_the_figure_plan_reachable(monkeypatch) -> None:
    assert _infographic_plan_applies(SimpleNamespace(), _style()) is False

    monkeypatch.setenv("AUTHORED_INFOGRAPHIC_PLAN_ENABLED", "true")
    assert _infographic_plan_applies(SimpleNamespace(), _style()) is True


def test_role_budgets_are_unchanged_by_the_extraction() -> None:
    # The table moved modules; not one number moved with it.
    assert role_budget("PROBLEM") == (64, 380, 460, 40)
    assert role_budget("COVER") == (58, 220, 280, 22)
    assert role_budget("SECTION") == (46, 150, 220, 12)
    assert role_budget("ARCHITECTURE") == (54, 390, 460, 48)
    assert role_budget("no-such-role") == DEFAULT_ROLE_BUDGET == (60, 380, 460, 40)
    assert role_budget("  problem  ") == role_budget("PROBLEM")


# --------------------------------------------------------------------------
# august: the pre-2026-08-28 contract, for ordinary decks only.
# --------------------------------------------------------------------------


def test_august_resolves_to_the_august_profile(monkeypatch) -> None:
    monkeypatch.setenv(_PROFILE_KNOB, "august")

    assert resolve_envelope_profile() is AUGUST_ENVELOPE_PROFILE


def test_august_overrides_the_per_rule_knobs(monkeypatch) -> None:
    monkeypatch.setenv(_PROFILE_KNOB, "august")
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_INFOGRAPHIC_PLAN_ENABLED", "true")

    envelope = resolve_envelope_profile()
    assert ordinary_envelope_enforced(envelope) is False
    assert ordinary_infographic_plan_enabled(envelope) is False
    assert _infographic_plan_applies(SimpleNamespace(), _style()) is False


def test_august_yields_the_august_limits(monkeypatch) -> None:
    monkeypatch.setenv(_PROFILE_KNOB, "august")
    # Even with the envelope knob on, an August deck states no budget.
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    plan = build_authored_capacity_plan(("a", "b"), ("PROBLEM", "CLOSING"), _ORDINARY)

    for slide in plan.slides:
        # No character, semantic-target or image budget is stated...
        assert slide.enforced is False
        assert slide.prompt_brief(_ORDINARY) == ""
        # ...so the 460-char / 40-qid envelope cannot be violated,
        assert capacity_violations(_DENSE_HTML, None, slide) == ()
        assert aggregate_owner_capacity_violations(_DENSE_HTML, slide) == ()
        # and the floor is the 12px capability-registry floor, not 15px:
        # the August medians were 12.5-16.5px.
        assert slide.min_font_px == 12
        assert slide.with_source_images(3).enforced is False
    assert plan.briefs() == ("", "")


def test_august_turns_the_blocking_dom_gate_off_for_ordinary_decks(monkeypatch) -> None:
    monkeypatch.setenv(_PROFILE_KNOB, "august")

    assert resolve_composition_profile(SimpleNamespace()).strict_dom_gate is False


def test_august_does_not_reach_the_source_fidelity_profiles(monkeypatch) -> None:
    monkeypatch.setenv(_PROFILE_KNOB, "august")
    slide = build_authored_capacity_plan(("a",), ("PROBLEM",), _VERBATIM).at(0)

    assert slide.enforced is True
    assert slide.min_font_px == 15
    assert "SERVER-FROZEN COMPOSITION ENVELOPE" in slide.prompt_brief(_VERBATIM)
    assert resolve_composition_profile(_reflow_request()).strict_dom_gate is True


# --------------------------------------------------------------------------
# The switch itself.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["augsut", "AUGUST", " august "])
def test_an_unknown_profile_is_a_configuration_error(monkeypatch, value) -> None:
    # The registry validates the enum on read, so a misspelling, a different
    # case, or stray space fails loudly instead of silently authoring under
    # ``current`` and invalidating the comparison.
    monkeypatch.setenv(_PROFILE_KNOB, value)

    with pytest.raises(ValueError, match="AUTHORED_COMPOSITION_ENVELOPE_PROFILE"):
        resolve_envelope_profile()


def test_a_profile_name_off_the_registry_path_is_still_refused() -> None:
    settings = SimpleNamespace(get_env=lambda key: "augsut")

    with pytest.raises(ValueError, match="authored_composition_envelope_profile_invalid"):
        get_authored_composition_envelope_profile(settings)  # type: ignore[arg-type]


def test_a_resolved_profile_cannot_be_mutated() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        CURRENT_ENVELOPE_PROFILE.ordinary_strict_dom_gate = False  # type: ignore[misc]


def test_an_explicit_envelope_overrides_the_ambient_one(monkeypatch) -> None:
    # The generation resolves the profile once and passes the object down; the
    # ambient environment must not be able to contradict it mid-operation.
    monkeypatch.setenv(_PROFILE_KNOB, "august")

    profile = resolve_composition_profile(
        SimpleNamespace(), envelope=CURRENT_ENVELOPE_PROFILE
    )
    assert profile.strict_dom_gate is True

    monkeypatch.setenv(_PROFILE_KNOB, "current")
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    slide = build_authored_capacity_plan(
        ("a",), ("PROBLEM",), _ORDINARY, envelope=AUGUST_ENVELOPE_PROFILE
    ).at(0)
    assert slide.enforced is False
    assert _infographic_plan_applies(
        SimpleNamespace(), _style(), AUGUST_ENVELOPE_PROFILE
    ) is False
