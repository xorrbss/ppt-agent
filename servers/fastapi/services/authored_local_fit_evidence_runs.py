"""Per-run font-size acceptance for the authored local-fit policy.

Extracted from ``authored_local_fit_evidence``. A fit report carries one
"run" per font-size candidate the fitter considered; these helpers resolve
the policy revision in effect, validate that a report's identity matches its
baseline (or raise), and turn a report's raw runs into the accepted-run
records a receipt is built from (folding in the extended-shrink-basis
evidence from ``authored_local_fit_evidence_shrink_basis``).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.authored_local_fit_evidence_shrink_basis import _extended_shrink_basis
from services.authored_local_fit_evidence_types import AuthoredLocalFitRejected
from services.authored_local_fit_identity import (
    LocalFitIdentityError,
    validate_immutable_identity,
)
from services.authored_local_fit_policy import (
    POLICY_REVISION,
    font_policy,
    font_size_allowed,
    policy_floor_pt,
)


def _run_id(run: Mapping[str, Any]) -> str:
    value = run.get("run_id", run.get("id"))
    return str(value or "")


def _validate_immutable_identity(
    baseline: Mapping[str, Any], report: Mapping[str, Any], *, reason: str
) -> None:
    try:
        validate_immutable_identity(baseline, report, reason=reason)
    except LocalFitIdentityError as exc:
        raise AuthoredLocalFitRejected(exc.reason) from exc


def _policy_revision(policy: Mapping[str, Any]) -> str:
    revision = str(policy.get("policy_revision") or POLICY_REVISION)
    try:
        font_policy(revision)
    except ValueError as exc:
        raise AuthoredLocalFitRejected("unsupported_policy_revision") from exc
    return revision


def _accepted_runs(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    policy_revision: str = POLICY_REVISION,
) -> list[dict[str, Any]]:
    _validate_immutable_identity(baseline, report, reason="reconstruction_mismatch")
    original_values = baseline.get("runs")
    final_values = report.get("runs")
    if not isinstance(original_values, list) or not isinstance(final_values, list):
        raise AuthoredLocalFitRejected("baseline_unavailable")
    originals = {
        _run_id(run): dict(run)
        for run in original_values
        if isinstance(run, Mapping) and _run_id(run)
    }
    finals = {
        _run_id(run): dict(run)
        for run in final_values
        if isinstance(run, Mapping) and _run_id(run)
    }
    if not originals or set(originals) != set(finals):
        raise AuthoredLocalFitRejected("baseline_unavailable")
    accepted: list[dict[str, Any]] = []
    for run_id in originals:
        original = originals[run_id]
        final = finals[run_id]
        try:
            original_pt = float(original["original_pt"])
            final_pt = float(final.get("final_pt", final.get("effective_pt")))
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthoredLocalFitRejected("unverifiable_font_size") from exc
        if not font_size_allowed(
            original_pt, final_pt, policy_revision=policy_revision,
        ):
            raise AuthoredLocalFitRejected(
                "font_floor_unfit"
                if final_pt < policy_floor_pt(policy_revision)
                else "shrink_limit"
            )
        accepted.append(
            {
                **original,
                **final,
                "run_id": run_id,
                "original_pt": original_pt,
                "final_pt": final_pt,
            }
        )
    _extended_shrink_basis(report, accepted, policy_revision=policy_revision)
    return accepted


__all__ = [
    "_run_id",
    "_validate_immutable_identity",
    "_policy_revision",
    "_accepted_runs",
]
