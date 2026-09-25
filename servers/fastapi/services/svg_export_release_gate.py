"""release-v1's review-receipt requirement, applied to svg's own quality receipt."""

from __future__ import annotations


def require_release_v1_receipt(receipt: dict) -> None:
    """Enforce release-v1's review-receipt requirement on svg's own quality receipt.

    Reads the ``visual_review`` field ``services.svg_export_quality.source_svg_public_quality_receipt``
    already produces (see ``services/svg_export_visual_review.py`` for how the
    agreed ``passed|flagged|failed|unavailable`` outcomes map onto the public
    contract, and for ``evidence.measurements.advisoryDigest`` binding the
    receipt to a real advisory run). This is a receipt-content check only -- it does not join the
    single-flight export identity authority (``services/public_export_identity.py``);
    that wiring is N1's (native-objects, Phase 2) to add once svg exports
    start using it.
    """
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
    # The receipt is the public wire dump (camelCase aliases), and the contract
    # forbids extra evidence keys: the digest travels in ``measurements``.
    visual_review = receipt.get("visualReview")
    if not isinstance(visual_review, dict):
        raise NonRetryableWorkspaceOperationError("svg_release_quality_receipt_invalid")
    if visual_review.get("status") != "passed":
        raise NonRetryableWorkspaceOperationError("svg_release_quality_visual_review_not_passed")
    evidence = visual_review.get("evidence")
    measurements = evidence.get("measurements") if isinstance(evidence, dict) else None
    if not isinstance(measurements, dict) or not measurements.get("advisoryDigest"):
        raise NonRetryableWorkspaceOperationError("svg_release_quality_advisory_evidence_missing")
    if receipt.get("releaseReadiness") != "ready":
        raise NonRetryableWorkspaceOperationError("svg_release_quality_not_release_ready")

__all__ = ["require_release_v1_receipt"]
