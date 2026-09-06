"""Sampled verification that a fitted authored slide's reconstruction agrees
with what the fitter measured when it decided the slide fit.

The in-render fitter (built in a parallel worktree; see
``docs/authored-in-render-fit-design-20260906.md`` sections 5, 8 and 9)
rewrites an authored slide's HTML in the browser so it fits its box and
returns that ``fitted_html`` alongside the metrics it measured while fitting.
Because the fit happens inside a live, stateful render, there is a real risk
that a *plain* re-render of the same ``fitted_html`` (no fit pass, i.e.
``fit=False``) disagrees with what was measured -- either because the fitted
markup itself is unstable across renders, or because the fit pass mutated the
DOM in a way that does not round-trip through serialization.

This module is the "prove it" half: for a sampled fraction of fitted slides,
re-render ``fitted_html`` plainly and compare its metrics against the
fitter's own capture. Disagreement is logged and, to avoid ever publishing a
PNG whose accompanying metrics no longer describe it, the verification
re-render is what gets returned instead of the fitter's capture.

This module owns only the verification pass. It does not build ``fit`` or
``fitted_html`` -- those come from ``utils.slide_capture`` once the parallel
fitter work lands there. Until then, this module reads those attributes via
``getattr(..., None)`` and is exercised with fake capture objects.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, fields as dataclass_fields, replace as dataclass_replace
from typing import Any, Mapping, MutableMapping

from utils.authored_dom_quality import inspect_authored_dom
from utils.authored_dom_text_escape import TEXT_ESCAPE_CATEGORY
from utils.slide_capture import RenderCapture, render_html_to_png_with_metrics

LOGGER = logging.getLogger(__name__)

# How many offending qids to name in the mismatch warning. Enough to debug
# from the log line alone without flooding it on a badly-broken slide.
_MAX_REPORTED_QIDS = 10

# Bounding boxes are compared in device pixels; sub-pixel jitter from
# rounding in the browser's own layout engine is expected and not a defect.
_BBOX_TOLERANCE_PX = 0.5


@dataclass(frozen=True)
class FitVerification:
    """Outcome of sampled reconstruction verification for one fitted slide."""

    verified: bool
    sampled: bool
    mismatches: tuple[str, ...]
    max_bbox_delta_px: float
    escape_detected: bool


def should_verify(sample: float, *, key: str) -> bool:
    """Deterministically decide whether ``key`` falls in the verified sample.

    The same ``key`` always yields the same decision at a given ``sample``
    rate, so a slide that recurs across retries or replays is either always
    verified or never verified at that rate -- there is no per-call
    randomness to chase. ``sample`` is a fraction in ``[0, 1]``: 0 means
    never verify, 1 means always verify.
    """

    if sample <= 0:
        return False
    if sample >= 1:
        return True
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    # A stable, uniformly distributed bucket in [0, 1) derived from the key.
    bucket = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return bucket < sample


def _not_sampled() -> FitVerification:
    return FitVerification(
        verified=False,
        sampled=False,
        mismatches=(),
        max_bbox_delta_px=0.0,
        escape_detected=False,
    )


def _element_bboxes_by_qid(metrics: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    """Extract ``{qid: bbox}`` from a raw browser metrics payload.

    Deliberately permissive about the input shape (mirrors the tolerance in
    ``utils.authored_dom_quality.normalize_browser_metrics``): a payload with
    no ``elements`` list, or elements missing a ``qid``/``bbox``, contributes
    nothing rather than raising, since the caller may hand this a metrics
    dict of unknown provenance.
    """

    if not isinstance(metrics, Mapping):
        return {}
    elements = metrics.get("elements")
    if not isinstance(elements, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in elements:
        if not isinstance(item, Mapping):
            continue
        qid = item.get("qid")
        bbox = item.get("bbox")
        if qid is None or not isinstance(bbox, Mapping):
            continue
        result[str(qid)] = bbox
    return result


def _has_text_escape(html: str, metrics: Mapping[str, Any] | None) -> bool:
    report = inspect_authored_dom(
        html,
        metrics,
        reference_mode=None,
        strict_structural_off_canvas=False,
    )
    return any(issue.category == TEXT_ESCAPE_CATEGORY for issue in report.issues)


def _compare_bboxes(
    original: Mapping[str, Mapping[str, Any]],
    verify: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[str, ...], float]:
    """Compare bboxes for qids present in both sides.

    A qid that only exists on one side (e.g. the fit pass added or removed an
    element) is not itself treated as a bbox mismatch here -- that is a
    structural difference the escape/DOM check above is better placed to
    catch. This only measures geometric drift of elements both captures
    agree exist.
    """

    mismatched_qids: list[str] = []
    max_delta = 0.0
    for qid in sorted(set(original) & set(verify)):
        original_bbox = original[qid]
        verify_bbox = verify[qid]
        qid_max_delta = 0.0
        for field in ("x", "y", "width", "height"):
            try:
                original_value = float(original_bbox.get(field, 0.0))
                verify_value = float(verify_bbox.get(field, 0.0))
            except (TypeError, ValueError):
                continue
            qid_max_delta = max(qid_max_delta, abs(original_value - verify_value))
        max_delta = max(max_delta, qid_max_delta)
        if qid_max_delta > _BBOX_TOLERANCE_PX:
            mismatched_qids.append(qid)
    return tuple(mismatched_qids), max_delta


def _carry_fit_fields(base: RenderCapture, *, source: object) -> RenderCapture:
    """Copy ``fitted_html``/``fit_report`` from ``source`` onto ``base``, if
    ``RenderCapture`` has grown those fields.

    Written defensively against the parallel fitter landing: today
    ``RenderCapture`` has neither field, so this is a no-op and ``base`` is
    returned unchanged.
    """

    field_names = {field.name for field in dataclass_fields(base)}
    updates: dict[str, Any] = {}
    if "fitted_html" in field_names:
        updates["fitted_html"] = getattr(source, "fitted_html", None)
    if "fit_report" in field_names:
        updates["fit_report"] = getattr(source, "fit_report", None)
    if not updates:
        return base
    return dataclass_replace(base, **updates)


async def verify_fitted_capture(
    capture: object,
    *,
    sample: float,
    timeout: int = 60,
) -> tuple[object, FitVerification]:
    """Re-render a sampled fraction of fitted slides to prove the fit held.

    ``capture`` is expected to expose ``fitted_html`` (``str | None``),
    ``html_digest`` and ``browser_metrics``, i.e. the ``RenderCapture``
    contract once the fitter lands ``fitted_html``/``fit_report``.

    Returns ``(capture_to_publish, verification)``. ``capture_to_publish`` is
    ``capture`` unchanged when the slide was not sampled or verification
    agreed; on disagreement it is a fresh capture built from the
    verification re-render (so its PNG and metrics always describe the same
    render), carrying over ``fitted_html``/``fit_report`` from ``capture``.
    """

    fitted_html = getattr(capture, "fitted_html", None)
    if fitted_html is None:
        return capture, _not_sampled()

    sample_key = getattr(capture, "html_digest", None) or fitted_html
    if not should_verify(sample, key=sample_key):
        return capture, _not_sampled()

    verify_capture = await render_html_to_png_with_metrics(
        fitted_html, timeout=timeout, fit=False
    )

    escape_detected = _has_text_escape(fitted_html, verify_capture.browser_metrics)
    original_bboxes = _element_bboxes_by_qid(getattr(capture, "browser_metrics", None))
    verify_bboxes = _element_bboxes_by_qid(verify_capture.browser_metrics)
    mismatched_qids, max_delta = _compare_bboxes(original_bboxes, verify_bboxes)

    if not escape_detected and not mismatched_qids:
        return capture, FitVerification(
            verified=True,
            sampled=True,
            mismatches=(),
            max_bbox_delta_px=max_delta,
            escape_detected=False,
        )

    mismatches: list[str] = []
    if escape_detected:
        mismatches.append(TEXT_ESCAPE_CATEGORY)
    mismatches.extend(mismatched_qids)
    reported_qids = mismatched_qids[:_MAX_REPORTED_QIDS]

    LOGGER.warning(
        "authored_fit_reconstruction_mismatch original_digest=%s verify_digest=%s "
        "max_bbox_delta_px=%.3f escape_detected=%s mismatched_qids=%s",
        getattr(capture, "html_digest", None),
        verify_capture.html_digest,
        max_delta,
        escape_detected,
        reported_qids,
    )

    result_capture = RenderCapture.from_payload(
        html_digest=verify_capture.html_digest,
        png=verify_capture.png,
        browser_metrics=verify_capture.browser_metrics,
        runtime_counters=verify_capture.runtime_counters,
    )
    result_capture = _carry_fit_fields(result_capture, source=capture)

    return result_capture, FitVerification(
        verified=False,
        sampled=True,
        mismatches=tuple(mismatches),
        max_bbox_delta_px=max_delta,
        escape_detected=escape_detected,
    )


def record_fit_verification(
    instruments: MutableMapping[str, int], verification: FitVerification
) -> None:
    """Update in-process counters with the outcome of one verification.

    ``fit_verified_slides`` counts slides that were actually sampled and
    checked; ``fit_reconstruction_mismatches`` counts how many of those
    disagreed. Both keys are created (at 0) if absent, so callers can read
    them unconditionally even before the first sampled slide.
    """

    instruments.setdefault("fit_verified_slides", 0)
    instruments.setdefault("fit_reconstruction_mismatches", 0)
    if not verification.sampled:
        return
    instruments["fit_verified_slides"] += 1
    if not verification.verified:
        instruments["fit_reconstruction_mismatches"] += 1


__all__ = [
    "FitVerification",
    "should_verify",
    "verify_fitted_capture",
    "record_fit_verification",
]
