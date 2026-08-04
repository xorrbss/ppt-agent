from services.authored_quality_review_service import _merge_dom_repair_triggers
from utils.authored_dom_quality import DomQualityIssue, DomQualityReport
from utils.llm_calls.critique_slide import SlideCritique


def _report(*issues: DomQualityIssue, telemetry_status="available") -> DomQualityReport:
    return DomQualityReport(
        reference_mode="style_reference",
        issues=issues,
        compact_dom=(),
        inspected_elements=1,
        telemetry_status=telemetry_status,
    )


def test_high_dom_clipping_triggers_repair_when_vqa_says_clean() -> None:
    clean_vqa = SlideCritique(needs_fix=False, issues=[])
    clipping = DomQualityIssue(
        category="clipping",
        severity="high",
        detail="top edge clips the title",
        qids=("sem:title",),
    )

    [merged] = _merge_dom_repair_triggers([clean_vqa], [_report(clipping)])

    assert merged is not None
    assert merged.needs_fix is True
    assert merged.issues[0].type == "other"
    assert merged.issues[0].detail.startswith("DOM_CLIPPING:")


def test_incomplete_dom_telemetry_does_not_fabricate_a_repair_issue() -> None:
    clean_vqa = SlideCritique(needs_fix=False, issues=[])

    [merged] = _merge_dom_repair_triggers(
        [clean_vqa],
        [_report(telemetry_status="incomplete")],
    )

    assert merged == clean_vqa
