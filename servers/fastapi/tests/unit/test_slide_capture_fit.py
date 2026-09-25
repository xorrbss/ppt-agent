"""API-side plumbing for the in-render card fit.

Covers the three seams that decide whether the persisted document and the
measured document are the same document: the opt-in class on the request copy,
the qid-complete precondition, and the capture assembly that makes the fitted
string the key its metrics are cached under.
"""

from __future__ import annotations

import hashlib

import pytest

from utils.slide_capture import _instrument_authored_html
from utils.slide_capture_fit import (
    FIT_CLASS,
    FIT_POLL_ATTEMPTS,
    FIT_POLL_INTERVAL_MS,
    fit_capture_fields,
    fit_marker_poll_js,
    fit_report_of,
    fit_request_html,
    html_digest,
    in_render_fit_probe_prelude,
    require_qid_complete,
)

QID_COMPLETE = '<html><body><div data-qid="card">x</div></body></html>'


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- the opt-in class ------------------------------------------------------


def test_the_class_is_added_to_an_html_element_without_one():
    assert fit_request_html('<html lang="ko"><body>x</body></html>') == (
        f'<html lang="ko" class="{FIT_CLASS}"><body>x</body></html>'
    )


def test_the_class_is_appended_to_an_existing_class_attribute():
    assert fit_request_html('<html class="deck dark"><body>x</body></html>') == (
        f'<html class="deck dark {FIT_CLASS}"><body>x</body></html>'
    )


def test_marking_the_request_copy_is_idempotent():
    once = fit_request_html('<html class="deck"><body>x</body></html>')
    assert fit_request_html(once) == once


def test_a_document_without_an_html_element_is_wrapped():
    assert fit_request_html("<div>x</div>") == (
        f'<!doctype html><html class="{FIT_CLASS}"><body><div>x</div></body></html>'
    )


def test_only_the_request_copy_carries_the_class():
    source = '<html lang="ko"><body>x</body></html>'
    fit_request_html(source)
    assert FIT_CLASS not in source


# --- the precondition ------------------------------------------------------


def test_qid_complete_html_passes_the_precondition():
    require_qid_complete(QID_COMPLETE)


def test_qid_incomplete_html_is_refused():
    with pytest.raises(ValueError, match="fit requires qid-complete HTML"):
        require_qid_complete("<html><body><div>x</div></body></html>")


# --- capture assembly ------------------------------------------------------


def _recording_cache(cached):
    """Behave like ``_cache_browser_metrics``: rebind ``source_digest`` to the key
    it stores under and hand the rebound mapping back to the caller."""

    def cache(key, metrics):
        rebound = {**dict(metrics), "source_digest": _digest(key)}
        cached.append((key, rebound))
        return rebound

    return cache



def test_without_fit_the_capture_fields_are_the_identity():
    cached: list[tuple[str, dict]] = []
    fields = fit_capture_fields(
        QID_COMPLETE,
        {"fit": {"edits": [{"qid": "card", "declarations": "height:1px"}]}},
        fit=False,
        digest=_digest(QID_COMPLETE),
        cache_metrics=_recording_cache(cached),
    )
    assert fields[:3] == (_digest(QID_COMPLETE), None, None)
    assert cached == []


def test_edits_key_the_metrics_under_the_fitted_string_and_nothing_else():
    report = {"edits": [{"qid": "card", "declarations": "height:471px"}]}
    metrics = {"fit": report, "elements": []}
    cached: list[tuple[str, dict]] = []
    digest, fitted_html, fit_report, bound = fit_capture_fields(
        QID_COMPLETE,
        metrics,
        fit=True,
        digest=_digest(QID_COMPLETE),
        cache_metrics=_recording_cache(cached),
    )
    assert fitted_html == (
        '<html><body><div data-qid="card" style="height:471px">x</div></body></html>'
    )
    assert digest == _digest(fitted_html) != _digest(QID_COMPLETE)
    assert fit_report is report
    assert [key for key, _ in cached] == [fitted_html]
    # The capture must carry metrics that identify the fitted document, or the
    # ledger and the gate cannot recognise it (bench run 45).
    assert bound["source_digest"] == _digest(fitted_html)


def test_a_fit_render_without_edits_keys_the_metrics_under_the_source():
    # The render itself keyed the metrics by the class-bearing request copy; with
    # no edits the measured DOM is the source DOM, so the source is the key every
    # later cache read uses.
    report = {"requested": True, "fit_state": "final", "edits": []}
    cached: list[tuple[str, dict]] = []
    digest, fitted_html, fit_report, bound = fit_capture_fields(
        QID_COMPLETE,
        {"fit": report},
        fit=True,
        digest=_digest(QID_COMPLETE),
        cache_metrics=_recording_cache(cached),
    )
    assert (digest, fitted_html, fit_report) == (_digest(QID_COMPLETE), None, report)
    assert [key for key, _ in cached] == [QID_COMPLETE]
    assert bound["source_digest"] == _digest(QID_COMPLETE)


def test_a_render_with_no_report_at_all_is_not_a_failure():
    cached: list[tuple[str, dict]] = []
    fields = fit_capture_fields(
        QID_COMPLETE, {"elements": []}, fit=True, digest=_digest(QID_COMPLETE),
        cache_metrics=_recording_cache(cached),
    )
    assert fields[:3] == (_digest(QID_COMPLETE), None, None)
    assert [key for key, _ in cached] == [QID_COMPLETE]


def test_fit_report_of_ignores_a_malformed_payload():
    assert fit_report_of(None) is None
    assert fit_report_of({"fit": "nope"}) is None
    assert fit_report_of({"fit": {"fit_state": "final"}}) == {"fit_state": "final"}


def test_html_digest_matches_the_capture_digest_format():
    assert html_digest(QID_COMPLETE) == _digest(QID_COMPLETE)


# --- the probe and the worker's marker read --------------------------------


def test_the_probe_template_still_renders_and_fits_before_it_measures():
    # Only the class-bearing request copy selects the in-render fitter.
    instrumented, _key = _instrument_authored_html(fit_request_html(QID_COMPLETE), issue_capability=False)
    assert "__codexFit" in instrumented
    apply_at = instrumented.index("__codexFit.apply(")
    payload_at = instrumented.index("const payload = {")
    assert apply_at < payload_at, "the fit pass must run before the measurement pass"
    # The payload carries the report on every render, and both passes are named.
    assert "metrics_cache_key: '" in instrumented and ", fit, elements" in instrumented
    # The shared observation script names both passes (``utils/slide_capture_local_fit.py``).
    assert "done(ready ? 'final' : 'provisional')" in instrumented


def test_the_unmarked_probe_injects_no_fitter():
    instrumented, _key = _instrument_authored_html(QID_COMPLETE, issue_capability=False)
    assert "const __codexFitPass = () => null;" in instrumented
    assert "__codexFit.apply(" not in instrumented


def test_the_probe_survives_a_fitter_that_throws():
    prelude = in_render_fit_probe_prelude()
    assert "try {" in prelude and "catch (error)" in prelude
    assert "requested: false" in prelude


def test_both_render_modules_poll_the_marker_for_a_final_fit():
    from utils import slide_capture_pipe_render, slide_capture_reusable_render

    for module in (slide_capture_pipe_render, slide_capture_reusable_render):
        source = module.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "fit_marker_poll_js(" in text, source

    poll = fit_marker_poll_js("__marker__")
    assert f"poll < {FIT_POLL_ATTEMPTS}" in poll and FIT_POLL_ATTEMPTS == 20
    assert f"setTimeout(resolve, {FIT_POLL_INTERVAL_MS})" in poll and FIT_POLL_INTERVAL_MS == 50
    assert "fit.fit_state === 'final'" in poll
    assert "fit.requested === false" in poll
    assert "!fit ||" in poll
    assert "getElementById('__marker__')" in poll
    assert "window.__codexObserveFinal()" in poll
    assert poll.rstrip().endswith("return content;")


def test_the_poll_indents_every_line_it_emits():
    poll = fit_marker_poll_js("__marker__", indent="    ")
    assert all(line.startswith("    ") for line in poll.splitlines())
