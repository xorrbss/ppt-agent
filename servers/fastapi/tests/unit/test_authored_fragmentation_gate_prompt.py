"""The fragmentation gate's design-system clause.

With the knob off the deck-invariant prefix must be byte-identical to the
pre-gate prompt; with it on exactly one countable clause is added, beside the
capability clause, and the frozen ``PRE_BUNDLE_TYPE_SCALES`` baseline is not
touched.
"""

from __future__ import annotations

from services.authored_vnext.capability_registry import prompt_capability_clause
from utils.authored_fragmentation_gate import fragmentation_prompt_clause
from utils.llm_calls.author_slide import Brand, build_design_system
from utils.llm_calls.author_slide_brief import PRE_BUNDLE_TYPE_SCALES

GATE_KNOB = "AUTHORED_FRAGMENTATION_GATE_ENABLED"
OWNERS_KNOB = "AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS"
FLOOR_KNOB = "AUTHORED_TYPE_FLOOR_PX"


def _brand() -> Brand:
    return Brand(topic="차세대 VOC 통합", language="Korean", primary="#0B4EA2")


def _clear(monkeypatch) -> None:
    for key in (GATE_KNOB, OWNERS_KNOB, FLOOR_KNOB):
        monkeypatch.delenv(key, raising=False)


def test_the_clause_is_empty_while_the_knob_is_off(monkeypatch):
    _clear(monkeypatch)
    assert fragmentation_prompt_clause() == ""
    assert "Fragmentation:" not in build_design_system(_brand())


def test_the_clause_is_the_only_difference_when_the_knob_is_on(monkeypatch):
    _clear(monkeypatch)
    before = build_design_system(_brand())

    monkeypatch.setenv(GATE_KNOB, "true")
    clause = fragmentation_prompt_clause()
    assert clause == (
        "\nFragmentation: use at most 50 text-owning elements on a slide; body type "
        "never below 15px; when copy does not fit, merge boxes rather than shrinking type."
    )
    after = build_design_system(_brand())
    capability = prompt_capability_clause()
    assert before.count(capability) == 1
    assert after == before.replace(capability, capability + clause)

    monkeypatch.setenv(OWNERS_KNOB, "40")
    monkeypatch.setenv(FLOOR_KNOB, "18")
    assert "at most 40 text-owning elements" in build_design_system(_brand())
    assert "never below 18px" in build_design_system(_brand())


def test_the_frozen_type_scale_baseline_is_untouched(monkeypatch):
    monkeypatch.setenv(GATE_KNOB, "true")
    assert "Fragmentation" not in str(PRE_BUNDLE_TYPE_SCALES)
    assert "merge boxes" not in str(PRE_BUNDLE_TYPE_SCALES)
