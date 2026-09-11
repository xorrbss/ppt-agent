"""Acceptance evidence for the authored local-fit policy.

The browser fitter proposes bytes and measurements.  This module is the
pipeline boundary that decides whether those bytes may enter a checkpoint.
Changed HTML is accepted only from one fresh, fit-disabled reconstruction;
unchanged HTML keeps the first capture and therefore pays no second render.
"""
from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from services.authored_local_fit_identity import (
    LocalFitIdentityError,
    validate_immutable_identity,
)
from services.authored_local_fit_policy import (
    GEOMETRY_TOLERANCE_PX,
    POLICY_REVISION,
    acceptance_key,
    baseline_digest,
    digest,
    html_digest,
    font_policy,
    font_size_allowed,
    policy_floor_pt,
)


RECEIPT_KEY = "authored_local_fit"
RECEIPT_SCHEMA = "authored-local-fit-acceptance/v1"
ORIGINAL_DELIVERY_REVISION = "authored-local-fit-original-delivery/v1"
UNCERTAIN_COMPARISON_REJECTIONS = frozenset(
    {
        "reject.budget_exhausted",
        "reject.font_extended_comparison_incomplete",
        "reject.font_unverifiable",
    }
)
PUBLISHABLE_VISUAL_REASONS = frozenset(
    {
        "parent_region_unavailable",
        "parent_geometry_unsupported",
        "parent_preservation_failed",
    }
)


class AuthoredLocalFitRejected(RuntimeError):
    """The deterministic fitter or its independent reconstruction failed."""

    def __init__(self, reason: str):
        self.reason = str(reason or "reconstruction_mismatch")
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class AcceptedLocalFit:
    html: str
    png: bytes
    metrics: dict[str, Any]
    receipt: dict[str, Any]


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


def _elements(metrics: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = metrics.get("elements")
    if not isinstance(raw, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        if qid:
            result[qid] = item
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_image_text_scope(metrics: Mapping[str, Any]) -> None:
    """Fail closed for visible raster/external image text without evidence."""

    for item in _elements(metrics).values():
        evidence = item.get("image_text_evidence")
        if isinstance(evidence, Mapping) and evidence.get("status") in {
            "no_text",
            "font_floor_verified",
        }:
            continue
        style = item.get("computed_style")
        background = (
            str(style.get("backgroundImage") or "")
            if isinstance(style, Mapping)
            else ""
        )
        visible = item.get("css_visible") is not False and (
            _number(item.get("viewport_intersection_ratio")) or 0
        ) > 0
        if visible and (
            str(item.get("tag") or "").lower() == "img"
            or ("url(" in background.lower() and background.lower() != "none")
        ):
            raise AuthoredLocalFitRejected("unverifiable_font_size")


def _compare_reconstruction(
    fitted_metrics: Mapping[str, Any], verify_metrics: Mapping[str, Any]
) -> tuple[float, tuple[str, ...]]:
    first, second = _elements(fitted_metrics), _elements(verify_metrics)
    if not first or set(first) != set(second):
        return 0.0, ("qid_set",)
    mismatches: list[str] = []
    max_delta = 0.0
    for qid in sorted(first):
        left, right = first[qid], second[qid]
        for field in ("owned_text", "rendered_text", "css_visible"):
            if left.get(field) != right.get(field):
                mismatches.append(f"{qid}:{field}")
        if left.get("computed_style") != right.get("computed_style"):
            mismatches.append(f"{qid}:computed_style")
        for field in ("effective_opacity", "viewport_intersection_ratio"):
            a, b = _number(left.get(field)), _number(right.get(field))
            if a is None or b is None or abs(a - b) > 1e-6:
                mismatches.append(f"{qid}:{field}")
        if left.get("owned_text") != right.get("owned_text"):
            mismatches.append(f"{qid}:text")
        left_box = left.get("bbox")
        right_box = right.get("bbox")
        if not isinstance(left_box, Mapping) or not isinstance(right_box, Mapping):
            mismatches.append(f"{qid}:bbox")
            continue
        for field in ("x", "y", "width", "height"):
            a, b = _number(left_box.get(field)), _number(right_box.get(field))
            if a is None or b is None:
                mismatches.append(f"{qid}:{field}")
                continue
            delta = abs(a - b)
            max_delta = max(max_delta, delta)
            if delta > GEOMETRY_TOLERANCE_PX:
                mismatches.append(f"{qid}:{field}")
        a, b = _number(left.get("font_size")), _number(right.get("font_size"))
        # Font floors have no geometry epsilon.  Browser readback itself is
        # compared at a small numeric precision to avoid JSON float noise.
        if a is None or b is None or abs(a - b) > 1e-6:
            mismatches.append(f"{qid}:font_size")
    return max_delta, tuple(dict.fromkeys(mismatches))


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


def _extended_shrink_basis(
    report: Mapping[str, Any],
    runs: list[Mapping[str, Any]],
    *,
    policy_revision: str,
) -> list[dict[str, Any]]:
    preferred = float(font_policy(policy_revision).max_shrink)
    extended = any(
        float(run["final_pt"]) < float(run["original_pt"]) * (1 - preferred) - 1e-9
        for run in runs
    )
    if not extended:
        return []
    if policy_revision != "authored-local-fit/v1.5":
        raise AuthoredLocalFitRejected("shrink_limit")
    candidates = report.get("candidates")
    candidate_records = [
        item for item in candidates if isinstance(item, Mapping)
    ] if isinstance(candidates, list) else []
    ids = [str(item.get("candidate_id") or "") for item in candidate_records]
    if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise AuthoredLocalFitRejected("extended_comparison_incomplete")
    by_id = dict(zip(ids, candidate_records, strict=True))
    committed = [
        item
        for item in candidate_records
        if item.get("outcome") == "committed"
        and item.get("extended_shrink") is True
    ]
    result: list[dict[str, Any]] = []
    for item in committed:
        basis = item.get("extended_shrink_basis")
        compared = basis.get("compared_candidate_ids") if isinstance(basis, Mapping) else None
        rejected = basis.get("rejected_candidate_ids") if isinstance(basis, Mapping) else None
        uncertain = basis.get("uncertain_candidate_ids") if isinstance(basis, Mapping) else None
        if (
            not isinstance(basis, Mapping)
            or basis.get("comparison_complete") is not True
            or basis.get("comparison_uncertain") is not False
            or not isinstance(compared, list)
            or not compared
            or not all(isinstance(value, str) and value for value in compared)
            or not isinstance(rejected, list)
            or not all(isinstance(value, str) and value for value in rejected)
            or not isinstance(uncertain, list)
            or uncertain
        ):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        linked_ids = [*compared, *rejected]
        if len(linked_ids) != len(set(linked_ids)) or any(
            candidate_id not in by_id for candidate_id in linked_ids
        ):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        root_qid = item.get("root_qid")
        selected_cost = item.get("cost")
        try:
            selected_total = float(selected_cost["total"])
        except (KeyError, TypeError, ValueError):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete") from None
        if not math.isfinite(selected_total):
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        related = [
            record
            for record in candidate_records
            if record.get("root_qid") == root_qid
            and record.get("extended_shrink") is not True
        ]
        passed_ids: set[str] = set()
        rejected_ids: set[str] = set()
        comparison_records: list[dict[str, Any]] = []
        for record in related:
            candidate_id = str(record.get("candidate_id"))
            outcome = str(record.get("outcome") or "")
            if outcome in {"passed", "committed"}:
                cost = record.get("cost")
                try:
                    total = float(cost["total"])
                except (KeyError, TypeError, ValueError):
                    raise AuthoredLocalFitRejected("extended_comparison_incomplete") from None
                if not math.isfinite(total) or total <= selected_total + 1e-10:
                    raise AuthoredLocalFitRejected("extended_comparison_incomplete")
                passed_ids.add(candidate_id)
                comparison_records.append(
                    {"candidateId": candidate_id, "outcome": outcome, "cost": total}
                )
                continue
            rejection = record.get("rejection")
            rejection_code = (
                str(rejection.get("code") or "")
                if isinstance(rejection, Mapping)
                else ""
            )
            if (
                not outcome.startswith(("rejected:", "removed:", "pruned:"))
                or not rejection_code.startswith("reject.")
                or rejection_code in UNCERTAIN_COMPARISON_REJECTIONS
            ):
                raise AuthoredLocalFitRejected("extended_comparison_incomplete")
            rejected_ids.add(candidate_id)
            comparison_records.append(
                {
                    "candidateId": candidate_id,
                    "outcome": outcome,
                    "rejectionCode": rejection_code,
                }
            )
        if set(compared) != passed_ids or set(rejected) != rejected_ids:
            raise AuthoredLocalFitRejected("extended_comparison_incomplete")
        result.append(
            {
                "candidateId": str(item.get("candidate_id") or ""),
                "comparisonComplete": True,
                "comparisonUncertain": False,
                "comparedCandidateIds": list(dict.fromkeys(compared))[:32],
                "rejectedCandidateIds": list(dict.fromkeys(rejected))[:64],
                "uncertainCandidateIds": [],
                "comparisonRecords": comparison_records[:96],
                "selectedCost": selected_total,
                "preferredMaxShrink": preferred,
                "floorPt": policy_floor_pt(policy_revision),
            }
        )
    if not result or any(not item["candidateId"] for item in result):
        raise AuthoredLocalFitRejected("extended_comparison_incomplete")
    return result


def extended_shrink_basis_valid(
    receipt: Mapping[str, Any], runs: list[Mapping[str, Any]], *, policy_revision: str
) -> bool:
    try:
        preferred = float(font_policy(policy_revision).max_shrink)
        extended = any(
            float(run["final_pt"])
            < float(run["original_pt"]) * (1 - preferred) - 1e-9
            for run in runs
        )
    except (KeyError, TypeError, ValueError):
        return False
    if not extended:
        return receipt.get("extendedShrinkBasis") in (None, [], ())
    basis = receipt.get("extendedShrinkBasis")
    if policy_revision != "authored-local-fit/v1.5" or not isinstance(basis, list) or not basis:
        return False
    for item in basis:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get("candidateId"), str)
            or not item.get("candidateId")
            or item.get("comparisonComplete") is not True
            or item.get("comparisonUncertain") is not False
            or item.get("preferredMaxShrink") != preferred
            or item.get("floorPt") != policy_floor_pt(policy_revision)
        ):
            return False
        compared = item.get("comparedCandidateIds")
        rejected = item.get("rejectedCandidateIds")
        uncertain = item.get("uncertainCandidateIds")
        records = item.get("comparisonRecords")
        if (
            not isinstance(compared, list)
            or not compared
            or not isinstance(rejected, list)
            or not isinstance(uncertain, list)
            or uncertain
            or not isinstance(records, list)
        ):
            return False
        linked = [*compared, *rejected]
        if (
            any(not isinstance(value, str) or not value for value in linked)
            or len(linked) != len(set(linked))
        ):
            return False
        record_by_id = {
            str(record.get("candidateId")): record
            for record in records
            if isinstance(record, Mapping) and record.get("candidateId")
        }
        if set(record_by_id) != set(linked) or len(record_by_id) != len(records):
            return False
        try:
            selected_cost = float(item["selectedCost"])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(selected_cost):
            return False
        for candidate_id in compared:
            record = record_by_id[candidate_id]
            if record.get("outcome") not in {"passed", "committed"}:
                return False
            try:
                cost = float(record["cost"])
                if not math.isfinite(cost) or cost <= selected_cost + 1e-10:
                    return False
            except (KeyError, TypeError, ValueError):
                return False
        for candidate_id in rejected:
            record = record_by_id[candidate_id]
            if (
                not str(record.get("outcome") or "").startswith(
                    ("rejected:", "removed:", "pruned:")
                )
                or not str(record.get("rejectionCode") or "").startswith("reject.")
                or str(record.get("rejectionCode") or "")
                in UNCERTAIN_COMPARISON_REJECTIONS
            ):
                return False
    return True


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


def _render_key(
    metrics: Mapping[str, Any],
    *,
    final_html: str,
    fit_mode: bool,
    policy_revision: str = POLICY_REVISION,
) -> str:
    identity = metrics.get("local_fit_render_identity")
    if not isinstance(identity, str) or not identity.startswith("sha256:"):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    return digest(
        {
            "html_digest": html_digest(final_html),
            "renderer_identity": identity,
            "fit_mode": bool(fit_mode),
            "policy_revision": policy_revision,
        }
    )


def _validated_png_dimensions(
    png: bytes, *, policy: Mapping[str, Any]
) -> tuple[int, int]:
    manifest = policy.get("export_scale_manifest")
    if not isinstance(manifest, Mapping):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    try:
        expected = (
            int(manifest["viewport_width"]),
            int(manifest["viewport_height"]),
        )
        with Image.open(io.BytesIO(png)) as image:
            actual = image.size
            if image.format != "PNG":
                raise AuthoredLocalFitRejected("reconstruction_mismatch")
            image.verify()
        with Image.open(io.BytesIO(png)) as decoded:
            decoded.load()
            if decoded.size != actual:
                raise AuthoredLocalFitRejected("reconstruction_mismatch")
    except AuthoredLocalFitRejected:
        raise
    except (KeyError, TypeError, ValueError, OSError, UnidentifiedImageError) as exc:
        raise AuthoredLocalFitRejected("reconstruction_mismatch") from exc
    if actual != expected:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    return actual


def _delivery_receipt(
    *,
    capture: object,
    original_html: str,
    policy: Mapping[str, Any],
    report: Mapping[str, Any],
    metrics: Mapping[str, Any],
    reasons: tuple[str, ...],
    rejected_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a fit-disabled original render without claiming quality approval."""

    policy_revision = _policy_revision(policy)
    png = bytes(capture.png)
    width, height = _validated_png_dimensions(png, policy=policy)
    render_key = _render_key(
        metrics,
        final_html=original_html,
        fit_mode=False,
        policy_revision=policy_revision,
    )
    from utils.slide_capture_local_fit import local_fit_render_identity

    if metrics.get("local_fit_render_identity") != local_fit_render_identity(
        original_html, policy, fit=False
    ):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    raw_baseline = report.get("baseline")
    baseline = dict(raw_baseline) if isinstance(raw_baseline, Mapping) else {}
    if baseline and (
        baseline.get("author_revision") != policy.get("author_revision")
        or baseline.get("original_html_digest")
        != policy.get("original_html_digest")
    ):
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    baseline_key = baseline_digest(baseline)
    rejection_evidence: dict[str, Any] = {}
    if isinstance(rejected_report, Mapping):
        candidates = rejected_report.get("candidates")
        rejection_evidence = {
            "status": str(rejected_report.get("status") or ""),
            "unresolvedReasons": [
                str(value)
                for value in rejected_report.get("unresolved_reasons", ())
                if value
            ][:8],
            "searchTermination": [
                {
                    key: value
                    for key, value in item.items()
                    if key in {"code", "root_qid", "evaluated_candidates", "unmeasured_candidates"}
                }
                for item in rejected_report.get("search_termination", ())
                if isinstance(item, Mapping)
            ][:8],
            "candidates": [
                {
                    "candidateId": str(item.get("candidate_id") or ""),
                    "outcome": str(item.get("outcome") or ""),
                    "rejectionCode": str(
                        (item.get("rejection") or {}).get("code") or ""
                    ) if isinstance(item.get("rejection"), Mapping) else "",
                }
                for item in (candidates if isinstance(candidates, list) else ())
                if isinstance(item, Mapping)
                and (item.get("outcome") != "passed")
            ][:16],
        }
    return {
        "schema": RECEIPT_SCHEMA,
        "policy_revision": policy_revision,
        "policyRevision": policy_revision,
        "slideIndex": int(policy.get("slide_index", 0)),
        "authorRevision": str(policy.get("author_revision")),
        "originalHtmlDigest": html_digest(original_html),
        "finalHtmlDigest": html_digest(original_html),
        "baselineDigest": baseline_key,
        "renderKey": render_key,
        "acceptanceKey": acceptance_key(render_key, baseline, policy),
        "status": "preserved_original_review",
        "selectionStatus": "preserved_original_review",
        "deliveryRevision": ORIGINAL_DELIVERY_REVISION,
        "deliveryStatus": "output_usable",
        "pageOutputReady": True,
        "qualityPass": False,
        "changed": False,
        "verifiedFitDisabled": False,
        "renderedFitDisabled": True,
        "verificationCount": 0,
        "maxBboxDeltaPx": 0.0,
        "qualityStatus": "warning",
        "unresolvedReasons": list(reasons),
        "rejectionEvidence": rejection_evidence,
        "operations": [],
        "baseline": baseline,
        "runs": [],
        "png_digest": "sha256:" + hashlib.sha256(png).hexdigest(),
        "pngSize": {"width": width, "height": height},
        "telemetry": {
            **dict(report.get("telemetry") or {}),
            "html_changed": False,
            "evidence_cache_hit": False,
        },
        "exportScaleManifest": dict(policy.get("export_scale_manifest") or {}),
        "export_scale_manifest": dict(policy.get("export_scale_manifest") or {}),
        "imageTextScopeRevision": str(policy.get("image_text_scope_revision") or ""),
    }


def _receipt(
    *,
    policy: Mapping[str, Any],
    baseline: Mapping[str, Any],
    final_html: str,
    report: Mapping[str, Any],
    final_metrics: Mapping[str, Any],
    final_png: bytes,
    changed: bool,
    verified: bool,
    max_bbox_delta_px: float,
    verify_wait_ms: float,
    verify_ms: float,
    quality_status: str = "passed",
    unresolved_reasons: tuple[str, ...] = (),
) -> dict[str, Any]:
    policy_revision = _policy_revision(policy)
    accepted_runs = _accepted_runs(
        report, baseline, policy_revision=policy_revision,
    )
    extended_basis = _extended_shrink_basis(
        report, accepted_runs, policy_revision=policy_revision
    )
    render_key = _render_key(
        final_metrics,
        final_html=final_html,
        fit_mode=not verified,
        policy_revision=policy_revision,
    )
    telemetry = dict(report.get("telemetry") or {})
    telemetry.update(
        {
            "verify_queue_wait_ms": round(max(0.0, verify_wait_ms), 3),
            "verify_ms": round(max(0.0, verify_ms), 3),
            "html_changed": bool(changed),
            "evidence_cache_hit": False,
        }
    )
    png_digest = "sha256:" + hashlib.sha256(final_png).hexdigest()
    return {
        "schema": RECEIPT_SCHEMA,
        "policy_revision": policy_revision,
        "policyRevision": policy_revision,
        "slideIndex": int(policy.get("slide_index", 0)),
        "authorRevision": str(policy.get("author_revision")),
        "originalHtmlDigest": str(policy.get("original_html_digest")),
        "finalHtmlDigest": html_digest(final_html),
        "baselineDigest": baseline_digest(baseline),
        "renderKey": render_key,
        "acceptanceKey": acceptance_key(render_key, baseline, policy),
        "status": str(report.get("status") or "unchanged"),
        "changed": bool(changed),
        "verifiedFitDisabled": bool(verified),
        "verificationCount": 1 if verified else 0,
        "maxBboxDeltaPx": round(max_bbox_delta_px, 6),
        "qualityStatus": quality_status,
        "unresolvedReasons": list(unresolved_reasons),
        "operations": list(report.get("operations") or ()),
        "baseline": dict(baseline),
        "runs": accepted_runs,
        "extendedShrinkBasis": extended_basis,
        "png_digest": png_digest,
        "telemetry": telemetry,
        "exportScaleManifest": dict(policy.get("export_scale_manifest") or {}),
        "export_scale_manifest": dict(policy.get("export_scale_manifest") or {}),
        "imageTextScopeRevision": str(policy.get("image_text_scope_revision") or ""),
    }


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


def receipt_is_preserved_original(receipt: Mapping[str, Any]) -> bool:
    reasons = receipt.get("unresolvedReasons")
    return (
        receipt.get("selectionStatus") == "preserved_original_review"
        and receipt.get("deliveryRevision") == ORIGINAL_DELIVERY_REVISION
        and receipt.get("deliveryStatus") == "output_usable"
        and receipt.get("pageOutputReady") is True
        and receipt.get("qualityPass") is False
        and receipt.get("qualityStatus") == "warning"
        and receipt.get("changed") is False
        and receipt.get("renderedFitDisabled") is True
        and receipt.get("verifiedFitDisabled") is False
        and isinstance(reasons, (list, tuple))
        and bool(reasons)
    )


def receipt_quality_passed(receipt: Mapping[str, Any]) -> bool:
    return (
        not receipt_is_preserved_original(receipt)
        and receipt.get("qualityStatus") in (None, "passed")
        and receipt.get("qualityPass") is not False
        and receipt.get("unresolvedReasons") in ([], ())
    )


def receipt_quality_is_publishable(receipt: Mapping[str, Any]) -> bool:
    return receipt_quality_passed(receipt) or receipt_is_preserved_original(receipt)


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
        or getattr(capture, "html_digest", None) != html_digest(final_html)
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
) -> AcceptedLocalFit:
    report = _report(fitted_capture)
    baseline = validate_changed_capture(
        fitted_capture, original_html=original_html, policy=policy,
    )
    final_html = getattr(fitted_capture, "fitted_html", None)
    if html_digest(final_html) != getattr(verify_capture, "html_digest", None):
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


def reusable_checkpoint_receipt(
    metrics: Mapping[str, Any] | None,
    *,
    original_html: str,
    final_html: str,
    slide_index: int,
    policy: Mapping[str, Any],
    final_png: bytes | None = None,
) -> Mapping[str, Any] | None:
    if not isinstance(metrics, Mapping):
        return None
    receipt = metrics.get(RECEIPT_KEY)
    if not isinstance(receipt, Mapping):
        return None
    baseline = receipt.get("baseline")
    policy_revision = _policy_revision(policy)
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("policyRevision") != policy_revision
        or receipt.get("policy_revision") != policy_revision
        or receipt.get("slideIndex") != int(slide_index)
        or receipt.get("originalHtmlDigest") != html_digest(original_html)
        or receipt.get("finalHtmlDigest") != html_digest(final_html)
        or not isinstance(baseline, Mapping)
        or not isinstance(receipt.get("acceptanceKey"), str)
        or not receipt_quality_is_publishable(receipt)
    ):
        return None
    if receipt.get("changed") is True and receipt.get("verifiedFitDisabled") is not True:
        return None
    from utils.slide_capture_local_fit import local_fit_render_identity

    preserved_original = receipt_is_preserved_original(receipt)
    if preserved_original and (
        final_png is None
        or receipt.get("png_digest")
        != "sha256:" + hashlib.sha256(final_png).hexdigest()
    ):
        return None
    fit_mode = False if preserved_original else receipt.get("changed") is not True
    current_identity = local_fit_render_identity(
        final_html, policy, fit=fit_mode
    )
    expected_render_key = digest(
        {
            "html_digest": html_digest(final_html),
            "renderer_identity": current_identity,
            "fit_mode": fit_mode,
            "policy_revision": policy_revision,
        }
    )
    if (
        receipt.get("baselineDigest") != baseline_digest(baseline)
        or receipt.get("renderKey") != expected_render_key
        or receipt.get("acceptanceKey")
        != acceptance_key(expected_render_key, baseline, policy)
    ):
        return None
    return receipt


def cache_accepted_capture(accepted: AcceptedLocalFit, *, timeout: int) -> None:
    """Publish only an accepted final tuple to the existing bounded caches."""

    from utils.slide_capture import RenderCapture, _cache_browser_metrics, _cache_capture

    _cache_browser_metrics(accepted.html, accepted.metrics)
    capture = RenderCapture.from_payload(
        html_digest=html_digest(accepted.html),
        png=accepted.png,
        browser_metrics=accepted.metrics,
        runtime_counters={},
        fit_report=None,
    )
    _cache_capture(accepted.html, timeout, capture)


__all__ = [
    "AcceptedLocalFit",
    "AuthoredLocalFitRejected",
    "ORIGINAL_DELIVERY_REVISION",
    "PUBLISHABLE_VISUAL_REASONS",
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
