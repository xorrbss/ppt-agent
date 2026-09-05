"""The receipt's capacity domain does not fail a deck against a budget it never stated.

Regression for 2026-09-05: with AUTHORED_COMPOSITION_ENVELOPE_ENABLED unset the
envelope is relaxed (``SlideCapacity.enforced`` False) — the prompt carries no
text/qid budget and the author-time and gate-time checks report none — but
``evaluate_capacity_browser_metrics`` still compared the final Chromium metrics
with the frozen 460-character / 40-qid limits, and a freely composed deck that
authored and rendered 10/10 was blocked with ``capacityStatus=blocked``.
"""

from __future__ import annotations

from services.authored_composition_policy import SlideCapacity
from services.authored_quality_receipt_v2 import evaluate_capacity_browser_metrics


def _dense_metrics(owners: int) -> dict[str, object]:
    elements = [
        {"qid": "title", "semantic_role": "title", "rendered_text": "제목", "owned_text": "제목",
         "has_owned_text": True, "font_size": 24, "font_loaded": True},
    ]
    for index in range(owners):
        elements.append(
            {"qid": f"body.{index}", "semantic_role": "body", "rendered_text": "본문 " * 5,
             "owned_text": "본문 " * 5, "has_owned_text": True, "font_size": 13, "font_loaded": True}
        )
    return {"elements": elements}


def _capacity(*, enforced: bool) -> SlideCapacity:
    return SlideCapacity(
        slide_index=3, role="PILLARS", title_max_chars=58, body_max_chars=380,
        max_visible_text_chars=460, max_images=8, max_qids=40,
        min_font_px=12, enforced=enforced,
    )


def test_a_relaxed_envelope_reports_no_budget_violation() -> None:
    # 55 body owners × 15 characters: over both the qid and the text budget.
    result = evaluate_capacity_browser_metrics(_dense_metrics(55), _capacity(enforced=False))
    assert result["browserObserved"] is True
    assert result["checks"] == {
        "text": True, "title": True, "body": True, "font": True, "qid": True, "image": True,
    }
    assert not [v for v in result["violations"] if v.startswith(("qids:", "visible_text:", "images:"))]


def test_a_bound_envelope_still_blocks_the_same_page() -> None:
    result = evaluate_capacity_browser_metrics(_dense_metrics(55), _capacity(enforced=True))
    assert result["checks"]["qid"] is False
    assert result["checks"]["text"] is False
    # 55 body owners plus the title: every text owner counts against the budget.
    assert any(v.startswith("qids:56>40") for v in result["violations"])
    assert any(v.startswith("visible_text:") for v in result["violations"])


def test_the_font_floor_is_still_measured_when_relaxed() -> None:
    metrics = _dense_metrics(3)
    metrics["elements"][1]["font_size"] = 11
    result = evaluate_capacity_browser_metrics(metrics, _capacity(enforced=False))
    assert result["checks"]["font"] is False


def test_a_plain_mapping_without_the_field_is_treated_as_enforced() -> None:
    limits = {"maxQids": 8, "maxImages": 1, "maxVisibleTextChars": 100, "minFontPx": 12}
    result = evaluate_capacity_browser_metrics(_dense_metrics(12), limits)
    assert result["checks"]["qid"] is False
