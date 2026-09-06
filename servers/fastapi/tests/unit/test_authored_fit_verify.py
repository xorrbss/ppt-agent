"""Sampled reconstruction verification for fitted authored slides.

``utils.authored_fit_verify`` re-renders a sampled fraction of fitted slides
plainly (``fit=False``) and checks the result still agrees with what the
fitter measured. These are pure-helper tests: ``render_html_to_png_with_metrics``
is monkeypatched to a stub so no Chromium is involved, and capture objects are
small fakes exposing only the attributes the module reads (``fitted_html``,
``html_digest``, ``browser_metrics``) -- ``RenderCapture`` itself does not yet
carry ``fitted_html``/``fit_report`` in this worktree; the parallel fitter
work adds those fields.

See ``docs/authored-in-render-fit-design-20260906.md`` sections 5, 8, 9.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from utils.authored_fit_verify import (
    FitVerification,
    record_fit_verification,
    should_verify,
    verify_fitted_capture,
)
from utils.authored_dom_text_escape import TEXT_ESCAPE_CATEGORY
from utils.slide_capture import RenderCapture


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeCapture:
    """Duck-types the subset of the ``RenderCapture`` contract this module
    reads, plus ``fitted_html``/``fit_report`` -- fields the parallel fitter
    work has not yet added to the real dataclass."""

    html_digest: str
    png: bytes = b"original-png-bytes"
    browser_metrics: Mapping[str, Any] | None = None
    fitted_html: str | None = None
    fit_report: Mapping[str, Any] | None = None
    runtime_counters: Mapping[str, int] = field(default_factory=dict)


def _element(qid: str, *, x: float, y: float, width: float, height: float) -> dict:
    return {
        "qid": qid,
        "tag": "div",
        "parent_qid": "",
        "bbox": {"x": x, "y": y, "width": width, "height": height},
        "computed_style": {"backgroundColor": "rgba(0, 0, 0, 0)"},
        "text_fragments": [],
    }


def _metrics(elements: list[dict]) -> dict:
    return {"viewport": {"width": 1280, "height": 720}, "elements": elements}


def _clean_metrics() -> dict:
    return _metrics(
        [
            _element("card", x=100.0, y=100.0, width=400.0, height=300.0),
            _element("headline", x=120.0, y=120.0, width=360.0, height=40.0),
        ]
    )


def _escaping_metrics() -> dict:
    """A text owner whose ink prints below its own card -- the shape
    ``find_text_escapes`` flags as ``text_escapes_card``."""

    card = {
        "qid": "s1-card",
        "tag": "section",
        "parent_qid": "",
        "bbox": {"x": 0.0, "y": 0.0, "width": 200.0, "height": 100.0},
        "computed_style": {"backgroundColor": "rgb(255, 255, 255)"},
        "text_fragments": [],
    }
    paragraph = {
        "qid": "s1-copy",
        "tag": "p",
        "parent_qid": "s1-card",
        "bbox": {"x": 10.0, "y": 80.0, "width": 180.0, "height": 40.0},
        "computed_style": {"backgroundColor": "rgba(0, 0, 0, 0)"},
        "has_owned_text": True,
        "font_size": 18.0,
        "text_fragments": [{"x": 10.0, "y": 80.0, "width": 180.0, "height": 40.0}],
    }
    return _metrics([card, paragraph])


def _patch_render(monkeypatch, capture: RenderCapture) -> None:
    async def fake_render(html, timeout=60, *, fit=False):
        assert fit is False
        return capture

    monkeypatch.setattr(
        "utils.authored_fit_verify.render_html_to_png_with_metrics", fake_render
    )


# ---------------------------------------------------------------------------
# should_verify
# ---------------------------------------------------------------------------


def test_should_verify_zero_is_never():
    for key in ("a", "b", "c", "", "slide-42"):
        assert should_verify(0.0, key=key) is False


def test_should_verify_one_is_always():
    for key in ("a", "b", "c", "", "slide-42"):
        assert should_verify(1.0, key=key) is True


def test_should_verify_is_deterministic_for_the_same_key():
    key = "sha256:deadbeef"
    first = should_verify(0.5, key=key)
    for _ in range(20):
        assert should_verify(0.5, key=key) is first


def test_should_verify_distributes_across_keys():
    # Not a statistical test -- just confirms the decision actually depends
    # on the key rather than collapsing to one constant value.
    keys = [f"slide-{i}" for i in range(200)]
    decisions = {should_verify(0.5, key=key) for key in keys}
    assert decisions == {True, False}


# ---------------------------------------------------------------------------
# verify_fitted_capture
# ---------------------------------------------------------------------------


def test_no_fitted_html_returns_capture_untouched(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not re-render when there is no fitted_html")

    monkeypatch.setattr("utils.authored_fit_verify.render_html_to_png_with_metrics", boom)

    capture = FakeCapture(html_digest="sha256:abc", fitted_html=None)
    result_capture, verification = asyncio.run(
        verify_fitted_capture(capture, sample=1.0)
    )

    assert result_capture is capture
    assert verification == FitVerification(
        verified=False,
        sampled=False,
        mismatches=(),
        max_bbox_delta_px=0.0,
        escape_detected=False,
    )


def test_not_sampled_returns_capture_untouched(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not re-render when the slide was not sampled")

    monkeypatch.setattr("utils.authored_fit_verify.render_html_to_png_with_metrics", boom)

    capture = FakeCapture(
        html_digest="sha256:abc",
        fitted_html="<div data-qid='card'></div>",
        browser_metrics=_clean_metrics(),
    )
    result_capture, verification = asyncio.run(
        verify_fitted_capture(capture, sample=0.0)
    )

    assert result_capture is capture
    assert verification.sampled is False
    assert verification.verified is False


def test_agreement_returns_original_capture(monkeypatch):
    original = FakeCapture(
        html_digest="sha256:original",
        fitted_html="<div data-qid='card'></div>",
        browser_metrics=_clean_metrics(),
    )
    verify_capture = RenderCapture.from_payload(
        html_digest="sha256:verify",
        png=b"verify-png-bytes",
        browser_metrics=_clean_metrics(),
        runtime_counters={},
    )
    _patch_render(monkeypatch, verify_capture)

    result_capture, verification = asyncio.run(
        verify_fitted_capture(original, sample=1.0)
    )

    assert result_capture is original
    assert verification.verified is True
    assert verification.sampled is True
    assert verification.mismatches == ()
    assert verification.escape_detected is False
    assert verification.max_bbox_delta_px == pytest.approx(0.0)


def test_bbox_delta_over_tolerance_flags_mismatch(monkeypatch):
    original = FakeCapture(
        html_digest="sha256:original",
        fitted_html="<div data-qid='card'></div>",
        browser_metrics=_clean_metrics(),
        fit_report={"scale": 0.95},
    )
    shifted = _clean_metrics()
    # Nudge the headline 1px in x -- over the 0.5px tolerance.
    shifted["elements"][1]["bbox"]["x"] += 1.0
    verify_capture = RenderCapture.from_payload(
        html_digest="sha256:verify",
        png=b"verify-png-bytes",
        browser_metrics=shifted,
        runtime_counters={},
    )
    _patch_render(monkeypatch, verify_capture)

    result_capture, verification = asyncio.run(
        verify_fitted_capture(original, sample=1.0)
    )

    assert verification.verified is False
    assert verification.sampled is True
    assert "headline" in verification.mismatches
    assert verification.max_bbox_delta_px == pytest.approx(1.0)
    assert verification.escape_detected is False

    # The returned capture is the verification re-render, not the original.
    assert result_capture is not original
    assert result_capture.html_digest == "sha256:verify"
    assert result_capture.png == b"verify-png-bytes"
    # fitted_html/fit_report carry over once RenderCapture grows those fields;
    # today they are simply absent, so getattr falls back to None.
    assert getattr(result_capture, "fitted_html", None) == getattr(
        original, "fitted_html", None
    ) or not hasattr(result_capture, "fitted_html")


def test_sub_tolerance_delta_does_not_flag_mismatch(monkeypatch):
    original = FakeCapture(
        html_digest="sha256:original",
        fitted_html="<div data-qid='card'></div>",
        browser_metrics=_clean_metrics(),
    )
    nudged = _clean_metrics()
    nudged["elements"][1]["bbox"]["x"] += 0.2
    verify_capture = RenderCapture.from_payload(
        html_digest="sha256:verify",
        png=b"verify-png-bytes",
        browser_metrics=nudged,
        runtime_counters={},
    )
    _patch_render(monkeypatch, verify_capture)

    result_capture, verification = asyncio.run(
        verify_fitted_capture(original, sample=1.0)
    )

    assert verification.verified is True
    assert result_capture is original


def test_escape_in_verify_metrics_flags_mismatch(monkeypatch):
    original = FakeCapture(
        html_digest="sha256:original",
        fitted_html="<div data-qid='s1-card'></div>",
        browser_metrics=_escaping_metrics(),
    )
    verify_capture = RenderCapture.from_payload(
        html_digest="sha256:verify",
        png=b"verify-png-bytes",
        browser_metrics=_escaping_metrics(),
        runtime_counters={},
    )
    _patch_render(monkeypatch, verify_capture)

    result_capture, verification = asyncio.run(
        verify_fitted_capture(original, sample=1.0)
    )

    assert verification.verified is False
    assert verification.sampled is True
    assert verification.escape_detected is True
    assert TEXT_ESCAPE_CATEGORY in verification.mismatches
    assert result_capture.html_digest == "sha256:verify"


# ---------------------------------------------------------------------------
# record_fit_verification
# ---------------------------------------------------------------------------


def test_record_creates_keys_when_absent_without_sampling():
    instruments: dict[str, int] = {}
    record_fit_verification(
        instruments,
        FitVerification(
            verified=False,
            sampled=False,
            mismatches=(),
            max_bbox_delta_px=0.0,
            escape_detected=False,
        ),
    )

    assert instruments == {"fit_verified_slides": 0, "fit_reconstruction_mismatches": 0}


def test_record_increments_verified_only_on_agreement():
    instruments: dict[str, int] = {}
    record_fit_verification(
        instruments,
        FitVerification(
            verified=True,
            sampled=True,
            mismatches=(),
            max_bbox_delta_px=0.0,
            escape_detected=False,
        ),
    )

    assert instruments["fit_verified_slides"] == 1
    assert instruments["fit_reconstruction_mismatches"] == 0


def test_record_increments_both_counters_on_mismatch():
    instruments: dict[str, int] = {"fit_verified_slides": 3, "fit_reconstruction_mismatches": 1}
    record_fit_verification(
        instruments,
        FitVerification(
            verified=False,
            sampled=True,
            mismatches=("headline",),
            max_bbox_delta_px=1.0,
            escape_detected=False,
        ),
    )

    assert instruments["fit_verified_slides"] == 4
    assert instruments["fit_reconstruction_mismatches"] == 2
