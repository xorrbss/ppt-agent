"""The figure headline must be typed as words, not as one run.

Regression for the 2026-09-05 finding: the planning model returned
``수용성이최우선`` where it meant ``수용성이 최우선``, and the headline broke
mid-word in the page's largest type. 27 of 206 planned headlines were affected.
"""

from __future__ import annotations

from models.authored_infographic_plan import (
    MAX_TAKEAWAY_CHARS,
    AuthoredInfographicPlan,
    AuthoredSlideInfographic,
    InfographicNode,
)
from utils.authored_infographic.takeaway_spacing import (
    is_jammed_takeaway,
    jammed_takeaways,
    plan_takeaway_spacing_report,
)
from utils.llm_calls.author_infographic_plan import _GUIDANCE

_NODES = (
    InfographicNode(label="현장 수용성"),
    InfographicNode(label="데이터 품질"),
    InfographicNode(label="일정 지연"),
)


def _slide(takeaway: str | None, index: int = 0) -> AuthoredSlideInfographic:
    return AuthoredSlideInfographic(
        index=index,
        archetype="matrix",
        weight="standard",
        takeaway=takeaway,
        nodes=_NODES,
    )


def test_the_observed_headlines_are_all_detected() -> None:
    # Every one of these was produced by the planning model on 2026-09-04.
    for text in (
        "수용성이최우선",
        "승인과배치필요",
        "예측이학습된다",
        "전담과협업병행",
        "이번주승인착수",
        "고객경험취약",
    ):
        assert is_jammed_takeaway(text), text


def test_a_correctly_spaced_headline_is_not_flagged() -> None:
    for text in (
        "수용성이 최우선",
        "18개월 단계 도입",
        "핵심지표 개선",
        "26개월 손익분기",
    ):
        assert not is_jammed_takeaway(text), text


def test_a_short_compound_is_not_flagged() -> None:
    # Korean compounds of a few syllables are ordinary words, not jams.
    for text in ("품질관리팀", "손익분기", "전지표개선", "확산단계"):
        assert not is_jammed_takeaway(text), text


def test_an_absent_or_latin_headline_is_never_flagged() -> None:
    assert not is_jammed_takeaway(None)
    assert not is_jammed_takeaway("")
    assert not is_jammed_takeaway("TimeToValue")


def test_the_report_counts_the_deck() -> None:
    plan = AuthoredInfographicPlan(
        slides=(
            _slide("수용성이최우선", 0),
            _slide("18개월 단계 도입", 1),
            _slide("승인과배치필요", 2),
            _slide(None, 3),
        )
    )
    assert jammed_takeaways(plan.slides) == ("수용성이최우선", "승인과배치필요")
    assert plan_takeaway_spacing_report(plan) == {
        "takeaways": 3,
        "jammed": 2,
        "examples": ["수용성이최우선", "승인과배치필요"],
    }


def test_the_plan_prompt_tells_the_model_to_keep_its_spaces() -> None:
    # The only real fix: Korean spacing cannot be restored by rule, so the
    # instruction is what has to hold. Pin it so it cannot vanish silently.
    assert "spaces do not count toward the limit" in _GUIDANCE
    assert "never joined to the next one" in _GUIDANCE
    assert str(MAX_TAKEAWAY_CHARS) in _GUIDANCE
