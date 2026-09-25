"""reviewPages has one rule, shared by every writer of a published SVG deck."""

from services.generation_core.quality.page_outcome import review_page_indices


def test_only_verified_and_passed_pages_leave_review():
    qualities = [
        {"verified": True, "passed": True, "classification": "passed"},
        {"verified": False, "passed": True},
        {"verified": True, "passed": False},
        {},
        "not-a-mapping",
    ]
    assert review_page_indices(qualities) == [1, 2, 3, 4]


def test_a_fully_classified_clean_deck_has_no_review_pages():
    assert review_page_indices([{"verified": True, "passed": True}] * 3) == []
