"""QUAL-06: body font median from already-collected browser metrics, receipt-only."""

from __future__ import annotations

from services.authored_font_signal import (
    body_font_median_px,
    body_font_median_signal,
    body_font_sizes_px,
)
from services.authored_quality_receipt_v2 import evaluate_capacity_browser_metrics


def _element(qid: str, role: str, size: float | None, text: str = "text", **extra):
    item = {"qid": qid, "semantic_role": role, "rendered_text": text, "font_loaded": True, **extra}
    if size is not None:
        item["font_size"] = size
    return item


def _metrics() -> dict[str, object]:
    return {
        "elements": [
            _element("title", "title", 40),
            _element("body-1", "body", 18),
            _element("body-2", "body-item", 16),
            _element("body-3", "body", 22),
            _element("body-4", "body", 20),
            # Supporting, decoration, image and empty owners never count.
            _element("caption", "supporting", 12),
            _element("frame", "decoration", 30),
            _element("figure", "image", 48, tag="img"),
            _element("wrapper", "body", 14, text="", has_owned_text=False),
            # Unmeasured font size is skipped, not treated as zero.
            _element("body-5", "body", None),
        ]
    }


def test_body_font_sizes_pick_only_body_text_owners() -> None:
    assert body_font_sizes_px(_metrics()["elements"]) == [16.0, 18.0, 20.0, 22.0]
    assert body_font_median_px(_metrics()) == 19.0


def test_slide_without_body_owner_has_no_median() -> None:
    assert body_font_median_px({"elements": [_element("title", "title", 40)]}) is None
    assert body_font_median_px({"elements": "not-a-list"}) is None
    assert body_font_median_px(None) is None


def test_deck_signal_is_index_aligned_and_medians_the_slide_medians() -> None:
    signal = body_font_median_signal(
        [
            _metrics(),
            {"elements": [_element("t", "title", 40)]},
            {"elements": [_element("b", "body", 24), _element("c", "body", 26)]},
        ]
    )
    assert signal["slides"] == [19.0, None, 25.0]
    assert signal["deck"] == 22.0
    assert signal["measuredSlideCount"] == 2
    assert body_font_median_signal([]) == {"slides": [], "deck": None, "measuredSlideCount": 0}


def test_capacity_evaluation_carries_the_median_without_a_new_check() -> None:
    limits = {"maxQids": 20, "maxImages": 2, "maxVisibleTextChars": 500, "minFontPx": 14}
    result = evaluate_capacity_browser_metrics(_metrics(), limits)
    assert result["bodyFontMedianPx"] == 19.0
    assert set(result["checks"]) == {"text", "title", "body", "font", "qid", "image"}
