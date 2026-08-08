"""B-4 vision-QA: the structured critique data contract (no LLM call)."""

import pytest
from pydantic import ValidationError

from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def test_clean_critique_defaults_to_empty_issues():
    c = SlideCritique(needs_fix=False)
    assert c.needs_fix is False
    assert c.issues == []


def test_critique_with_issue():
    c = SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="overflow", severity="high", detail="title text is clipped")],
    )
    assert c.issues[0].type == "overflow"
    assert c.issues[0].severity == "high"


def test_invalid_issue_type_rejected():
    with pytest.raises(ValidationError):
        CritiqueIssue(type="banana", severity="high", detail="x")


def test_invalid_severity_rejected():
    with pytest.raises(ValidationError):
        CritiqueIssue(type="overflow", severity="critical", detail="x")


def test_issues_list_capped_at_8():
    issues = [CritiqueIssue(type="other", severity="low", detail=str(i)) for i in range(9)]
    with pytest.raises(ValidationError):
        SlideCritique(needs_fix=True, issues=issues)
