"""Receipt construction for the authored local-fit policy.

Extracted from ``authored_local_fit_evidence``. These builders turn a
verified capture (or, for the delivery path, an independently rendered
original) into the persisted receipt dict that downstream checkpoint and
export code reads back via ``reusable_checkpoint_receipt``.
"""
from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

from PIL import Image, UnidentifiedImageError

from services.authored_local_fit_evidence_runs import _accepted_runs, _policy_revision
from services.authored_local_fit_evidence_shrink_basis import _extended_shrink_basis
from services.authored_local_fit_evidence_types import (
    AuthoredLocalFitRejected,
    ORIGINAL_DELIVERY_REVISION,
    RECEIPT_SCHEMA,
)
from services.authored_local_fit_policy import (
    POLICY_REVISION,
    acceptance_key,
    baseline_digest,
    digest,
    html_digest,
)
from utils.hashing import sha256_prefixed


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
        "png_digest": sha256_prefixed(png),
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
    png_digest = sha256_prefixed(final_png)
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


__all__ = [
    "_render_key",
    "_validated_png_dimensions",
    "_delivery_receipt",
    "_receipt",
]
