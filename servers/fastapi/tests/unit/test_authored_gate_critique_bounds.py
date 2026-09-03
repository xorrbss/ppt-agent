"""A slide with many defects must reach repair, not kill the deck.

``SlideCritique.issues`` accepts at most eight entries. The deterministic
composition gate reports one issue per offending element, so a slide with four
overflowing boxes produces four clipping findings and four overflow findings,
and operation b3e18cc2 raised

    ValidationError: issues List should have at most 8 items after validation,
    not 12

while building the repair instruction. Authoring had just finished 35 of 35
slides; the deck died on the way to fixing one of them.
"""

from __future__ import annotations

import pytest

from services.authored_presentation_generation import (
    _MAX_CRITIQUE_ISSUES,
    _capacity_gate_initial_critiques,
)


def _element_issue(category: str, severity: str, detail: str, qid: str) -> dict:
    return {
        "category": category,
        "severity": severity,
        "detail": detail,
        "qids": [qid],
        "policy": "style_reference",
    }


def _slide_10_failure() -> list[dict[str, object]]:
    """The gate output that ended operation b3e18cc2, element for element."""

    issues = [
        _element_issue(
            "clipping", "medium", "browser reports clipped visible content", qid
        )
        for qid in ("a", "b", "c", "d")
    ]
    issues += [
        _element_issue(
            "overflow", "high", "content scroll dimensions exceed its client box", qid
        )
        for qid in ("a", "b", "c", "d")
    ]
    issues += [
        _element_issue("overlap", "high", f"element {qid} overlaps a sibling", qid)
        for qid in ("a", "b", "c", "d")
    ]
    return [{"slide_index": 9, "issues": issues}]


def test_repeated_findings_collapse_instead_of_failing_the_deck() -> None:
    critiques = _capacity_gate_initial_critiques(
        _slide_10_failure(), list(range(12)), ["PILLARS"] * 12
    )
    critique = critiques[9]
    assert critique is not None and critique.needs_fix
    assert len(critique.issues) <= _MAX_CRITIQUE_ISSUES
    details = [issue.detail for issue in critique.issues]
    assert len(details) == len(set(details)), "identical instructions must collapse"
    # The findings themselves survive: one instruction per distinct defect.
    assert any("clipped visible content" in detail for detail in details)
    assert any("scroll dimensions exceed" in detail for detail in details)
    assert sum("overlaps a sibling" in detail for detail in details) == 4
    # And each says which elements the gate measured it on.
    clipping = next(d for d in details if "clipped visible content" in d)
    assert "Elements: a" in clipping
    assert "+" in clipping or clipping.count(",") >= 3


def test_an_instruction_never_cuts_an_element_name_in_half() -> None:
    long_qids = [f"source-block-{index:016x}" for index in range(6)]
    issues = [
        _element_issue(
            "overflow", "high", "content scroll dimensions exceed its client box", qid
        )
        for qid in long_qids
    ]
    critiques = _capacity_gate_initial_critiques(
        [{"slide_index": 0, "issues": issues}], [0], ["PILLARS"]
    )
    detail = critiques[0].issues[0].detail
    assert len(detail) <= 200
    assert detail.endswith("header text.")
    listed = [qid for qid in long_qids if qid in detail]
    assert listed, "at least one element must be named"
    assert f"+{len(long_qids) - len(listed)} more" in detail


def test_a_slide_over_the_cap_is_still_repaired() -> None:
    issues = [
        _element_issue("overlap", "high", f"element {index} overlaps a sibling", str(index))
        for index in range(20)
    ]
    critiques = _capacity_gate_initial_critiques(
        [{"slide_index": 0, "issues": issues}], [0], ["PILLARS"]
    )
    critique = critiques[0]
    assert critique is not None
    # Twenty elements reporting twenty different defects stay twenty
    # instructions until the cap, which is what the cap is for.
    assert len(critique.issues) == _MAX_CRITIQUE_ISSUES
    # The gate's own reporting order is kept, so the repair sees a prefix of
    # what it found rather than an arbitrary sample.
    assert "element 0 overlaps" in critique.issues[0].detail


@pytest.mark.parametrize("count", [1, _MAX_CRITIQUE_ISSUES])
def test_a_slide_within_the_cap_is_unchanged(count: int) -> None:
    issues = [
        _element_issue("overlap", "high", f"element {index} overlaps a sibling", str(index))
        for index in range(count)
    ]
    critiques = _capacity_gate_initial_critiques(
        [{"slide_index": 0, "issues": issues}], [0], ["PILLARS"]
    )
    assert critiques[0] is not None
    assert len(critiques[0].issues) == count


def test_a_slide_with_no_findings_gets_no_critique() -> None:
    critiques = _capacity_gate_initial_critiques([], [0, 1], ["PILLARS", "PILLARS"])
    assert critiques == [None, None]
