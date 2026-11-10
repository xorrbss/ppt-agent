"""The vision-QA model half of the authored repair loop must stay reachable.

Recorded 2026-09-06, fixed 2026-09-07: ``contrast`` fired on 97.79% of 905
stored bench slides, so the deterministic critique was never empty and the
model call in ``revise_authored_deck`` was never made -- on the run 48 deck, 0
of 24 slides could reach it.  These tests pin both halves of the repair: which
categories may stand in for the model, and that the receipt can tell "the model
reviewed N slides" apart from "the deterministic critique produced N findings".
"""


from utils.authored_dom_categories import (
    deterministic_findings_supersede_vqa,
)
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
    inspect_authored_dom,
)
from services.authored_visual_review_evidence import visual_review_outcome

_VALID_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1 data-qid='q-title'>제목</h1></body></html>"
)


def _issue(category: str, *, severity: str = "medium") -> DomQualityIssue:
    return DomQualityIssue(category, severity, f"{category} measured", ("q-title",))


def _report(*issues: DomQualityIssue, status: str = "available") -> DomQualityReport:
    return DomQualityReport(
        reference_mode=STYLE_REFERENCE,
        issues=tuple(issues),
        compact_dom=(),
        inspected_elements=1,
        telemetry_status=status,
    )


# ---------------------------------------------------------------------------
# Which categories may stand in for the model
# ---------------------------------------------------------------------------


def test_an_unknown_category_supersedes_so_a_new_detector_stays_fail_closed():
    assert deterministic_findings_supersede_vqa(["a_category_added_next_month"]) is True


# ---------------------------------------------------------------------------
# Non-superseding findings brief the model; they do not trigger a repair
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The detector itself: contrast is a statement about glyphs this element paints
# ---------------------------------------------------------------------------


def _metrics(*, has_owned_text: bool) -> dict:
    return {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "q-title",
                "tag": "div",
                "bbox": {"x": 10, "y": 10, "width": 200, "height": 40},
                "font_size": 16,
                "contrast_ratio": 2.1,
                "has_owned_text": has_owned_text,
                # A wrapper still *renders* its descendants' text; what it does
                # not do is paint glyphs of its own, which is what a contrast
                # ratio is a statement about.
                "rendered_text": "제목",
                "computed_style": {"color": "rgb(8,145,110)", "backgroundColor": "rgb(255,255,255)"},
            }
        ],
    }


def test_contrast_is_reported_for_an_element_that_paints_its_own_text():
    report = inspect_authored_dom(_VALID_HTML, _metrics(has_owned_text=True))

    assert [issue.category for issue in report.issues] == ["contrast"]


def test_contrast_is_not_reported_for_a_wrapper_that_paints_no_text():
    report = inspect_authored_dom(_VALID_HTML, _metrics(has_owned_text=False))

    assert [issue.category for issue in report.issues] == []


# ---------------------------------------------------------------------------
# End to end through the repair loop
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Reachability is observable in the receipt
# ---------------------------------------------------------------------------


def test_the_receipt_separates_slides_entering_the_stage_from_slides_the_model_saw():
    evidence = visual_review_outcome(
        slide_count=24,
        fixed=[1],
        accepted_fixed=[1],
        review_stats={"vqaReviewedSlideCount": 20, "deterministicOnlySlideCount": 4},
    )

    assert evidence["reviewedSlideCount"] == 24
    assert evidence["modelReviewedSlideCount"] == 20


def test_a_run_that_reported_no_reachability_stats_says_so_rather_than_claiming_zero():
    evidence = visual_review_outcome(slide_count=3, fixed=[], accepted_fixed=[])

    assert evidence["modelReviewedSlideCount"] is None
