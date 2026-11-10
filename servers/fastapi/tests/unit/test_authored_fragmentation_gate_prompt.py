"""The fragmentation gate's design-system clause.

With the knob off the deck-invariant prefix must be byte-identical to the
pre-gate prompt; with it on exactly one countable clause is added, beside the
capability clause, and the frozen ``PRE_BUNDLE_TYPE_SCALES`` baseline is not
touched.
"""

from __future__ import annotations

from utils.llm_calls.author_slide_brief import PRE_BUNDLE_TYPE_SCALES

GATE_KNOB = "AUTHORED_FRAGMENTATION_GATE_ENABLED"
OWNERS_KNOB = "AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS"
FLOOR_KNOB = "AUTHORED_TYPE_FLOOR_PX"


def _clear(monkeypatch) -> None:
    for key in (GATE_KNOB, OWNERS_KNOB, FLOOR_KNOB):
        monkeypatch.delenv(key, raising=False)


def test_the_frozen_type_scale_baseline_is_untouched(monkeypatch):
    monkeypatch.setenv(GATE_KNOB, "true")
    assert "Fragmentation" not in str(PRE_BUNDLE_TYPE_SCALES)
    assert "merge boxes" not in str(PRE_BUNDLE_TYPE_SCALES)
