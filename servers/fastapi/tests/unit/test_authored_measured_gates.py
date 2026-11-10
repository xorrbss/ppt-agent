"""The measured gates reach the model where it can act, and stay furniture-safe.

Review of 2026-09-08 on the text-authority branch found three defects before
any bench run: the 15px clamp raised the 12px page number past the 14px
detection threshold (the fitter's content line and the footer gate both
moved), the "merge boxes" instruction only reached the local patch round
(which cannot restructure), and gate issues were listed before real DOM
overflow so the remake brief's two evidence slots quoted counts, not qids.
"""

from __future__ import annotations

from utils.authored_dom_footer_zone import _looks_like_page_number
from utils.authored_fragmentation_gate import inspect_fragmentation, page_number_max_font_px
from utils.slide_capture_fit import in_render_fit_probe_prelude
from utils.slide_capture_fit_in_render_script import fit_constants

GATE = "AUTHORED_FRAGMENTATION_GATE_ENABLED"


def _clear(monkeypatch) -> None:
    for key in (GATE, "AUTHORED_TYPE_FLOOR_PX", "AUTHORED_FRAGMENTATION_MAX_TEXT_OWNERS"):
        monkeypatch.delenv(key, raising=False)


def _page_number(font_px: float) -> dict:
    return {
        "qid": "s3-page",
        "bbox": {"x": 0, "y": 690, "width": 1280, "height": 16},
        "rendered_text": "03",
        "has_owned_text": True,
        "font_size": font_px,
    }


def test_page_number_raised_to_the_floor_is_still_furniture(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "true")
    assert page_number_max_font_px() == 15.0
    item = _page_number(15.0)
    assert _looks_like_page_number(item, viewport_width=1280, viewport_height=720)
    metrics = {"viewport": {"width": 1280, "height": 720}, "elements": [item]}
    report = inspect_fragmentation(metrics, max_text_owners=50, floor_px=15)
    assert report.text_owners == 0
    assert report.violations == ()


def test_page_number_threshold_stays_at_14px_when_the_gate_is_off(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "false")
    assert page_number_max_font_px() == 14.0
    assert not _looks_like_page_number(_page_number(15.0), viewport_width=1280, viewport_height=720)
    assert fit_constants()["page_number_max_font_px"] == 14.0


def test_fitter_probe_carries_the_raised_threshold(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(GATE, "true")
    prelude = in_render_fit_probe_prelude()
    assert '"page_number_max_font_px": 15.0' in prelude
    assert '"font_floor_px": 15.0' in prelude
