"""The vision-QA model half of the authored repair loop must stay reachable.

Recorded 2026-09-06, fixed 2026-09-07: ``contrast`` fired on 97.79% of 905
stored bench slides, so the deterministic critique was never empty and the
model call in ``revise_authored_deck`` was never made -- on the run 48 deck, 0
of 24 slides could reach it.  These tests pin both halves of the repair: which
categories may stand in for the model, and that the receipt can tell "the model
reviewed N slides" apart from "the deterministic critique produced N findings".
"""

import asyncio

from utils.authored_dom_categories import (
    VQA_SUPPRESSING_CATEGORIES,
    deterministic_findings_supersede_vqa,
)
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
    inspect_authored_dom,
)
from services.generation_core.authoring import author_vision_qa
from services.generation_core.authoring.author_slide import Brand
from utils.llm_calls.author_vqa_critique_routing import (
    deterministic_critique_supersedes_vqa,
    with_advisory_findings,
)
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
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


def test_contrast_alone_never_supersedes_the_model_call():
    assert "contrast" not in VQA_SUPPRESSING_CATEGORIES
    assert deterministic_critique_supersedes_vqa(_report(_issue("contrast"))) is False


def test_an_advisory_only_finding_never_supersedes_the_model_call():
    assert (
        deterministic_critique_supersedes_vqa(
            _report(_issue("text_intrudes_footer_zone"))
        )
        is False
    )


def test_a_measured_geometry_defect_still_supersedes_the_model_call():
    assert deterministic_critique_supersedes_vqa(_report(_issue("overflow"))) is True
    assert (
        deterministic_critique_supersedes_vqa(
            _report(_issue("contrast"), _issue("clipping"))
        )
        is True
    )


def test_an_unknown_category_supersedes_so_a_new_detector_stays_fail_closed():
    assert deterministic_findings_supersede_vqa(["a_category_added_next_month"]) is True


def test_unavailable_telemetry_routes_to_the_model_rather_than_standing_in():
    unmeasured = _report(_issue("overflow"), status="unavailable")

    assert deterministic_critique_supersedes_vqa(unmeasured) is False


# ---------------------------------------------------------------------------
# Non-superseding findings brief the model; they do not trigger a repair
# ---------------------------------------------------------------------------


def test_advisory_findings_join_a_critique_the_model_already_raised():
    advisory = SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="low-contrast", severity="medium", detail="3.97:1")],
    )
    model = SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="overlap", severity="high", detail="cards collide")],
    )

    merged = with_advisory_findings(model, advisory)

    assert merged is not None
    assert [issue.type for issue in merged.issues] == ["overlap", "low-contrast"]


def test_advisory_findings_do_not_turn_a_clean_model_verdict_into_a_repair():
    advisory = SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="low-contrast", severity="medium", detail="3.97:1")],
    )

    assert with_advisory_findings(SlideCritique(needs_fix=False), advisory).needs_fix is False
    assert with_advisory_findings(None, advisory) is None


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


def _run_repair(monkeypatch, report: DomQualityReport, model_verdict: SlideCritique):
    calls: list[int] = []

    async def fake_critique_authored(pngs, **_kwargs):
        calls.append(len(pngs))
        return [model_verdict]

    async def fake_author(*_args, **_kwargs):
        return _VALID_HTML.replace("제목", "고친 제목")

    async def fake_render(_html, timeout=60):  # noqa: ASYNC109 - stub mirrors API
        return b"repaired-png"

    monkeypatch.setattr(author_vision_qa, "inspect_authored_dom", lambda *a, **k: report)
    monkeypatch.setattr(author_vision_qa, "critique_authored", fake_critique_authored)
    monkeypatch.setattr(author_vision_qa, "author_slide_html", fake_author)
    monkeypatch.setattr(author_vision_qa, "render_html_to_png", fake_render)

    stats: dict = {}
    htmls, _pngs, fixed = asyncio.run(
        author_vision_qa.revise_authored_deck(
            [_VALID_HTML],
            [b"original-png"],
            ["content"],
            ["CONTENT"],
            Brand(topic="topic"),
            "design",
            review_stats_sink=stats,
        )
    )
    return calls, fixed, htmls, stats


def test_a_contrast_only_report_still_reaches_the_model(monkeypatch):
    clean = SlideCritique(needs_fix=False, issues=[])

    calls, fixed, htmls, stats = _run_repair(monkeypatch, _report(_issue("contrast")), clean)

    # The model was asked, said the slide is fine, and no re-author was spent.
    assert calls == [1]
    assert fixed == []
    assert htmls == [_VALID_HTML]
    assert stats["vqaReviewedSlideCount"] == 1
    assert stats["deterministicOnlySlideCount"] == 0


def test_a_measured_geometry_defect_still_skips_the_model(monkeypatch):
    clean = SlideCritique(needs_fix=False, issues=[])

    calls, fixed, _htmls, stats = _run_repair(monkeypatch, _report(_issue("overflow")), clean)

    assert calls == []
    assert fixed == [0]
    assert stats["vqaReviewedSlideCount"] == 0
    assert stats["deterministicOnlySlideCount"] == 1


def test_the_model_verdict_drives_the_repair_when_it_is_reached(monkeypatch):
    defect = SlideCritique(
        needs_fix=True,
        issues=[CritiqueIssue(type="overlap", severity="high", detail="cards collide")],
    )

    calls, fixed, htmls, stats = _run_repair(monkeypatch, _report(_issue("contrast")), defect)

    assert calls == [1]
    assert fixed == [0]
    assert "고친 제목" in htmls[0]
    assert stats["vqaReviewedSlideCount"] == 1


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
