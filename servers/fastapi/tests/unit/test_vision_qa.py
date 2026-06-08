"""B-4 vision-QA orchestration: the pure slides_needing_fix selector (no IO)."""

from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.llm_calls.vision_qa import slides_needing_fix


def test_selects_only_flagged_slides():
    results = [
        (0, SlideCritique(needs_fix=False)),
        (1, SlideCritique(needs_fix=True, issues=[CritiqueIssue(type="overflow", severity="high", detail="clipped")])),
        (2, None),  # review failed -> not selected
        (3, SlideCritique(needs_fix=True)),
    ]
    assert slides_needing_fix(results) == [1, 3]


def test_empty_when_all_clean():
    results = [(0, SlideCritique(needs_fix=False)), (1, None)]
    assert slides_needing_fix(results) == []
