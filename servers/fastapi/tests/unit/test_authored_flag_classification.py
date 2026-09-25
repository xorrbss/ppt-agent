"""A flag says one of two things, and only one of them asks for a reviewer."""

import pytest

from services.authored_flag_classification import (
    CLASSIFICATION_KEY,
    REPAIR_NOT_ADMITTED,
    REVIEW_REQUIRED,
    classify_flagged_slides,
    flag_pages,
    review_pages_for,
)

GATE_REASON = "composition_safety:gate_repair_not_admitted"

# run-20 of the verbatim 35-slide fixture, as
# artifacts/flagged-overreport-20260911/report.md measured it: 18 flagged
# slides, 8 of which carry a blocking defect on the bytes that shipped, and one
# of those 8 also failed the browser source readback's visibility check.
_RUN_20_FLAGGED = (2, 6, 9, 10, 14, 15, 16, 18, 19, 20, 22, 26, 27, 28, 30, 31, 32, 33)
_RUN_20_RESIDUAL = (2, 6, 10, 18, 19, 30, 32, 33)
_RUN_20_VISIBILITY = 33


def _flag(index: int, **extra: object) -> dict[str, object]:
    return {
        "index": index,
        "reason": GATE_REASON,
        "remakeAttempted": False,
        "remakeOutcome": "verbatim_flag_only",
        **extra,
    }


def _run_20_deck() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    flagged = [
        _flag(
            index,
            **(
                {
                    "reasons": [GATE_REASON, "source_visibility"],
                    "sourceVisibilityBlockIds": ["source-slide:34:text:4"],
                }
                if index == _RUN_20_VISIBILITY
                else {}
            ),
        )
        for index in _RUN_20_FLAGGED
    ]
    residual = [
        {"slide_index": index, "issues": [{"category": "overlap"}]}
        for index in _RUN_20_RESIDUAL
    ]
    return flagged, residual


def test_the_run_20_deck_sends_eight_of_its_eighteen_flags_to_review() -> None:
    flagged, residual = _run_20_deck()

    classified = classify_flagged_slides(flagged, residual_failures=residual)
    pages = review_pages_for(classified)

    assert len(classified) == 18, "no entry may be dropped"
    assert [page["index"] for page in pages] == list(_RUN_20_RESIDUAL)
    assert sum(1 for entry in classified if entry[CLASSIFICATION_KEY] == REVIEW_REQUIRED) == 8
    assert sum(1 for entry in classified if entry[CLASSIFICATION_KEY] == REPAIR_NOT_ADMITTED) == 10


def test_a_flag_the_final_gate_does_not_contradict_is_history_not_a_defect() -> None:
    classified = classify_flagged_slides([_flag(7)], residual_failures=[])

    assert classified[0][CLASSIFICATION_KEY] == REPAIR_NOT_ADMITTED
    assert review_pages_for(classified) == []


def test_a_residual_issue_on_the_published_bytes_asks_for_a_reviewer() -> None:
    classified = classify_flagged_slides(
        [_flag(7)], residual_failures=[{"slide_index": 7}]
    )

    assert classified[0][CLASSIFICATION_KEY] == REVIEW_REQUIRED
    assert review_pages_for(classified) == [{"index": 7, "reasons": [GATE_REASON]}]


def test_a_visibility_failure_asks_for_a_reviewer_without_any_residual() -> None:
    """The readback merges its sub-reason onto an already flagged slide."""

    entry = _flag(
        7,
        reasons=[GATE_REASON, "source_visibility"],
        sourceVisibilityBlockIds=["source-slide:8:text:2"],
    )

    classified = classify_flagged_slides([entry], residual_failures=[])

    assert classified[0][CLASSIFICATION_KEY] == REVIEW_REQUIRED
    assert review_pages_for(classified) == [
        {"index": 7, "reasons": [GATE_REASON, "source_visibility"]}
    ]


def test_an_author_stage_placeholder_always_asks_for_a_reviewer() -> None:
    """The page that shipped is not the page the deck authored.

    ``authored_author_stage_placeholder.py`` renders the outline deterministically
    when authoring gives up, so the slide is geometrically clean and leaves no
    residual for any gate to measure. Reading only the published bytes would hide
    the one outcome a reviewer most needs to see.
    """

    entry = {
        "index": 0,
        "reason": "author:attempts_exhausted",
        "remakeAttempted": True,
        "remakeOutcome": "author_rescue_budget_exhausted",
    }

    classified = classify_flagged_slides([entry], residual_failures=[])

    assert classified[0][CLASSIFICATION_KEY] == REVIEW_REQUIRED
    assert [page["index"] for page in review_pages_for(classified)] == [0]


@pytest.mark.parametrize(
    "reason", ["author:attempts_exhausted", "author:output_token_limit"]
)
def test_every_author_stage_reason_asks_for_a_reviewer(reason: str) -> None:
    entry = {
        "index": 0,
        "reason": reason,
        "remakeAttempted": False,
        "remakeOutcome": "not_attempted",
    }

    classified = classify_flagged_slides([entry], residual_failures=[])

    assert classified[0][CLASSIFICATION_KEY] == REVIEW_REQUIRED


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("verbatim_flag_only", REPAIR_NOT_ADMITTED),
        ("not_attempted", REPAIR_NOT_ADMITTED),
        ("placeholder", REVIEW_REQUIRED),
        ("remake_gate_failed", REVIEW_REQUIRED),
        ("remake_budget_exhausted", REVIEW_REQUIRED),
    ],
)
def test_only_a_preserved_original_can_be_history(outcome: str, expected: str) -> None:
    """Any outcome but a preserved original means a substitute shipped."""

    classified = classify_flagged_slides(
        [_flag(4, remakeOutcome=outcome)], residual_failures=[]
    )

    assert classified[0][CLASSIFICATION_KEY] == expected


def test_a_remake_that_ran_asks_for_a_reviewer_even_when_it_passed() -> None:
    """Its output replaced the authored attempt, which a reader should see."""

    classified = classify_flagged_slides(
        [_flag(4, remakeAttempted=True, remakeOutcome="remake_gate_failed")],
        residual_failures=[],
    )

    assert classified[0][CLASSIFICATION_KEY] == REVIEW_REQUIRED


def test_classification_preserves_every_existing_field() -> None:
    """Consumers keep reading the vocabulary they read before."""

    entry = _flag(3, slideId="abc", remakeCount=2, repeated=True)

    classified = classify_flagged_slides([entry], residual_failures=[])[0]

    assert {key: classified[key] for key in entry} == entry
    assert set(classified) == set(entry) | {CLASSIFICATION_KEY}


def test_flag_pages_without_the_filter_is_the_shape_the_receipt_always_had() -> None:
    flagged, _ = _run_20_deck()

    pages = flag_pages(flagged)

    assert [page["index"] for page in pages] == list(_RUN_20_FLAGGED)
    assert all(set(page) == {"index", "reasons"} for page in pages)
    assert pages[0]["reasons"] == [GATE_REASON]
    assert pages[-1]["reasons"] == [GATE_REASON, "source_visibility"]


def test_reasons_are_deduplicated_and_capped_at_eight() -> None:
    entry = _flag(1, reasons=[f"reason:{i // 2}" for i in range(24)])

    pages = flag_pages([entry])

    assert len(pages[0]["reasons"]) == 8
    assert len(set(pages[0]["reasons"])) == 8


@pytest.mark.parametrize("index", [None, True, "4", 1.5])
def test_an_entry_without_an_integer_index_never_reaches_a_reviewer(index) -> None:
    classified = classify_flagged_slides(
        [{"index": index, "reason": GATE_REASON}], residual_failures=[]
    )

    assert len(classified) == 1, "the entry is kept"
    assert flag_pages(classified) == []


def test_the_e4_receipt_validator_admits_a_classified_entry() -> None:
    """The published receipt is validated before a benchmark reads it."""

    from services.authored_generation_timing_receipt_e4 import flagged_slide_entries

    flagged, residual = _run_20_deck()
    classified = classify_flagged_slides(flagged, residual_failures=residual)

    validated = flagged_slide_entries(classified)

    assert len(validated) == 18
