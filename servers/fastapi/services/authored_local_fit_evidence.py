"""Acceptance evidence for the authored local-fit policy.

The browser fitter proposes bytes and measurements.  This module is the
pipeline boundary that decides whether those bytes may enter a checkpoint.
Changed HTML is accepted only from one fresh, fit-disabled reconstruction;
unchanged HTML keeps the first capture and therefore pays no second render.

This module is a thin orchestrator: the comparison, run-acceptance,
receipt-building, and status-predicate logic live in sibling
``authored_local_fit_evidence_*`` modules (see ``docs/file-size-policy.md``).
Every public name below stays importable from here so existing callers and
tests do not need to change.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.authored_local_fit_evidence_receipts import _delivery_receipt, _receipt
from services.authored_local_fit_evidence_reconstruction import (
    _compare_reconstruction,
    _validate_image_text_scope,
)
from services.authored_local_fit_evidence_runs import (
    _accepted_runs,
    _policy_revision,
    _validate_immutable_identity,
)
from services.authored_local_fit_evidence_shrink_basis import (
    extended_shrink_basis_valid,
)
from services.authored_local_fit_evidence_status import (
    receipt_is_preserved_original,
    receipt_quality_is_publishable,
    receipt_quality_passed,
    reusable_checkpoint_receipt,
)
from services.authored_local_fit_evidence_types import (
    AcceptedLocalFit,
    AuthoredLocalFitRejected,
    ORIGINAL_DELIVERY_REVISION,
    RECEIPT_KEY,
)
from services.authored_local_fit_policy import html_digest


def _report(capture: object) -> Mapping[str, Any]:
    report = getattr(capture, "fit_report", None)
    if not isinstance(report, Mapping):
        raise AuthoredLocalFitRejected("unverifiable_font_size")
    return report


def _metrics(capture: object) -> dict[str, Any]:
    value = getattr(capture, "browser_metrics", None)
    if not isinstance(value, Mapping):
        raise AuthoredLocalFitRejected("unverifiable_font_size")
    return dict(value)


def _baseline(report: Mapping[str, Any], *, policy: Mapping[str, Any]) -> dict[str, Any]:
    value = report.get("baseline")
    if not isinstance(value, Mapping) or value.get("complete") is not True:
        raise AuthoredLocalFitRejected("baseline_unavailable")
    result = dict(value)
    if (
        result.get("author_revision") != policy.get("author_revision")
        or result.get("original_html_digest") != policy.get("original_html_digest")
    ):
        raise AuthoredLocalFitRejected("baseline_unavailable")
    return result


def _unresolved_reason(report: Mapping[str, Any]) -> str | None:
    reasons = report.get("unresolved_reasons")
    if isinstance(reasons, (list, tuple)) and reasons:
        return str(reasons[0])
    error = report.get("error")
    if error:
        return str(error)
    if report.get("partial") is True or report.get("status") == "unresolved":
        return "unsupported_geometry"
    return None


def _verify_runs(
    accepted: list[Mapping[str, Any]], verify_report: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> None:
    _validate_immutable_identity(
        baseline, verify_report, reason="reconstruction_mismatch",
    )
    values = verify_report.get("runs")
    if not isinstance(values, list):
        raise AuthoredLocalFitRejected("unverifiable_font_size")
    verify = {
        _run_id(run): run
        for run in values
        if isinstance(run, Mapping) and _run_id(run)
    }
    if {str(run["run_id"]) for run in accepted} != set(verify):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    for run in accepted:
        other = verify[str(run["run_id"])]
        value = other.get("final_pt", other.get("effective_pt"))
        try:
            if abs(float(run["final_pt"]) - float(value)) > 1e-6:
                raise AuthoredLocalFitRejected("reconstruction_mismatch")
        except (TypeError, ValueError) as exc:
            raise AuthoredLocalFitRejected("unverifiable_font_size") from exc


def _run_id(run: Mapping[str, Any]) -> str:
    value = run.get("run_id", run.get("id"))
    return str(value or "")


def accept_unchanged_capture(
    capture: object, *, original_html: str, policy: Mapping[str, Any]
) -> AcceptedLocalFit:
    report, metrics = _report(capture), _metrics(capture)
    policy_revision = _policy_revision(policy)
    unresolved = report.get("unresolved_reasons")
    fitter_reasons = tuple(
        str(reason).strip()
        for reason in (unresolved if isinstance(unresolved, (list, tuple)) else ())
        if str(reason).strip()
    )
    if report.get("status") == "unresolved" and fitter_reasons:
        # The fitter restored the original and named why its search stopped
        # (``candidates_exhausted``, ``budget_exhausted``, ...). That is a
        # search outcome, not a reconstruction failure: keep the fitter's own
        # word so the receipt does not read as a renderer identity mismatch.
        raise AuthoredLocalFitRejected(fitter_reasons[0])
    if (
        report.get("requested") is not True
        or report.get("fit_state") != "final"
        or report.get("status") != "unchanged"
        or report.get("changed") is not False
        or report.get("policy_revision") != policy_revision
        or metrics.get("local_fit_mode") is not True
        or getattr(capture, "html_digest", None) != html_digest(original_html)
        or policy.get("original_html_digest") != html_digest(original_html)
        or report.get("partial") is not False
        or report.get("error") not in (None, "")
        or not isinstance(unresolved, (list, tuple))
        or bool(unresolved)
    ):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    _validate_image_text_scope(metrics)
    fitted_html = getattr(capture, "fitted_html", None)
    changed = bool(report.get("changed")) or (
        isinstance(fitted_html, str) and fitted_html != original_html
    )
    if changed:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    baseline = _baseline(report, policy=policy)
    _accepted_runs(
        report, baseline, policy_revision=policy_revision,
    )
    receipt = _receipt(
        policy=policy,
        baseline=baseline,
        final_html=original_html,
        report=report,
        final_metrics=metrics,
        final_png=bytes(capture.png),
        changed=False,
        verified=False,
        max_bbox_delta_px=0.0,
        verify_wait_ms=0.0,
        verify_ms=0.0,
    )
    metrics[RECEIPT_KEY] = receipt
    return AcceptedLocalFit(original_html, bytes(capture.png), metrics, receipt)


def retain_publishable_unresolved_capture(
    capture: object,
    *,
    original_html: str,
    policy: Mapping[str, Any],
    unresolved_reasons: tuple[str, ...] = (),
    rejected_report: Mapping[str, Any] | None = None,
) -> AcceptedLocalFit:
    """Keep an independently rendered original without approving its quality."""

    report, metrics = _report(capture), _metrics(capture)
    raw_reasons = report.get("unresolved_reasons")
    reasons = tuple(
        dict.fromkeys(
            str(reason).strip()
            for reason in (
                unresolved_reasons
                or (raw_reasons if isinstance(raw_reasons, (list, tuple)) else ())
            )
            if str(reason).strip()
        )
    )
    policy_revision = _policy_revision(policy)
    if (
        not reasons
        or report.get("requested") is not False
        or report.get("fit_state") != "final"
        or report.get("policy_revision") != policy_revision
        or metrics.get("local_fit_mode") is not False
        or getattr(capture, "html_digest", None) != html_digest(original_html)
        or policy.get("original_html_digest") != html_digest(original_html)
        or bool(report.get("changed"))
        or bool(report.get("operations"))
        or bool(report.get("edits"))
    ):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    fitted_html = getattr(capture, "fitted_html", None)
    if fitted_html is not None:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    png = bytes(capture.png)
    receipt = _delivery_receipt(
        capture=capture,
        original_html=original_html,
        policy=policy,
        report=report,
        metrics=metrics,
        reasons=reasons,
        rejected_report=rejected_report,
    )
    metrics[RECEIPT_KEY] = receipt
    return AcceptedLocalFit(original_html, png, metrics, receipt)


def validate_changed_capture(
    capture: object, *, original_html: str, policy: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate fit-enabled candidate evidence before any recovery decision."""

    report, metrics = _report(capture), _metrics(capture)
    policy_revision = _policy_revision(policy)
    final_html = getattr(capture, "fitted_html", None)
    if (
        report.get("requested") is not True
        or report.get("fit_state") != "final"
        or report.get("changed") is not True
        or report.get("policy_revision") != policy_revision
        or metrics.get("local_fit_mode") is not True
        or not isinstance(final_html, str)
        or final_html == original_html
        or ((getattr(capture, "html_digest", None) != html_digest(original_html) or report.get("candidate_html_digest") != html_digest(final_html)) if report.get("fit_proposal") is True else (getattr(capture, "html_digest", None) != html_digest(final_html)))
        or policy.get("original_html_digest") != html_digest(original_html)
    ):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    unresolved = report.get("unresolved_reasons")
    if (
        report.get("partial") is not False
        or report.get("error") not in (None, "")
        or not isinstance(unresolved, (list, tuple))
        or _unresolved_reason(report) is not None
    ):
        raise AuthoredLocalFitRejected("candidate_evidence_invalid")
    _validate_image_text_scope(metrics)
    baseline = _baseline(report, policy=policy)
    _accepted_runs(report, baseline, policy_revision=policy_revision)
    return baseline


def accept_verified_capture(
    fitted_capture: object,
    verify_capture: object,
    *,
    original_html: str,
    policy: Mapping[str, Any],
    verify_queued_at: float,
    verify_started_at: float,
    verify_finished_at: float,
    prepared_html: str | None = None,
) -> AcceptedLocalFit:
    report = _report(fitted_capture)
    baseline = validate_changed_capture(
        fitted_capture, original_html=original_html, policy=policy,
    )

    # The proposal retains its original digest; only fresh verification can
    # establish evidence for the policy-normalized output bytes.
    final_html = prepared_html if prepared_html is not None else getattr(fitted_capture, "fitted_html", None)
    if (html_digest(final_html) != getattr(verify_capture, "html_digest", None)):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    verify_report = _report(verify_capture)
    verify_metrics = _metrics(verify_capture)
    verify_reasons = verify_report.get("unresolved_reasons")
    if (
        verify_report.get("requested") is not False
        or verify_report.get("fit_state") != "final"
        or verify_report.get("changed") is not False
        or verify_report.get("policy_revision") != _policy_revision(policy)
        or verify_metrics.get("local_fit_mode") is not False
        or getattr(verify_capture, "fitted_html", None) is not None
        or verify_report.get("partial") is not False
        or bool(verify_report.get("error"))
        or not isinstance(verify_reasons, (list, tuple))
        or _unresolved_reason(verify_report) is not None
    ):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    verify_baseline = _baseline(verify_report, policy=policy)
    _validate_immutable_identity(
        baseline, verify_baseline, reason="reconstruction_mismatch",
    )
    accepted_runs = _accepted_runs(
        report, baseline, policy_revision=_policy_revision(policy),
    )
    _verify_runs(accepted_runs, verify_report, baseline)
    fitted_metrics = _metrics(fitted_capture)
    _validate_image_text_scope(verify_metrics)
    max_delta, mismatches = _compare_reconstruction(fitted_metrics, verify_metrics)
    if mismatches:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    receipt = _receipt(
        policy=policy,
        baseline=baseline,
        final_html=final_html,
        report=report,
        final_metrics=verify_metrics,
        final_png=bytes(verify_capture.png),
        changed=True,
        verified=True,
        max_bbox_delta_px=max_delta,
        verify_wait_ms=(verify_started_at - verify_queued_at) * 1000,
        verify_ms=(verify_finished_at - verify_started_at) * 1000,
    )
    verify_metrics[RECEIPT_KEY] = receipt
    return AcceptedLocalFit(
        final_html, bytes(verify_capture.png), verify_metrics, receipt
    )


def cache_accepted_capture(
    accepted: AcceptedLocalFit, *, timeout: int,
    width_px: int = 1280, height_px: int = 720,
) -> None:
    """Publish only an accepted final tuple to the existing bounded caches."""

    from utils.slide_capture import RenderCapture, _cache_browser_metrics, _cache_capture

    _cache_browser_metrics(
        accepted.html, accepted.metrics,
        width_px=width_px, height_px=height_px,
    )
    capture = RenderCapture.from_payload(
        html_digest=html_digest(accepted.html),
        png=accepted.png,
        browser_metrics=accepted.metrics,
        runtime_counters={},
        fit_report=None,
    )
    _cache_capture(
        accepted.html, timeout, capture,
        width_px=width_px, height_px=height_px,
    )


__all__ = [
    "AcceptedLocalFit",
    "AuthoredLocalFitRejected",
    "ORIGINAL_DELIVERY_REVISION",
    "RECEIPT_KEY",
    "accept_unchanged_capture",
    "accept_verified_capture",
    "cache_accepted_capture",
    "extended_shrink_basis_valid",
    "receipt_quality_is_publishable",
    "receipt_is_preserved_original",
    "receipt_quality_passed",
    "retain_publishable_unresolved_capture",
    "reusable_checkpoint_receipt",
    "validate_changed_capture",
]
