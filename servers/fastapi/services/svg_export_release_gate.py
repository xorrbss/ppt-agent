"""release-v1's review-receipt requirement, applied to svg's own quality receipt."""

from __future__ import annotations


def require_release_v1_receipt(receipt: dict) -> None:
    """Enforce release-v1's review-receipt requirement on svg's own quality receipt.

    Reads the ``visualReview`` field ``services.svg_export_quality.source_svg_public_quality_receipt``
    already produces (see ``C1-quality.md`` in the session design folder for the
    agreed field contract: ``status`` in ``passed|flagged|failed|unavailable``,
    plus ``evidence.advisoryDigest`` binding the receipt to a real advisory
    run). Both receipt builders this session's callers use
    (``source_svg_public_quality_receipt`` and the legacy-fidelity fallback)
    dump ``QualityReceiptV3`` with ``by_alias=True``, so the wire keys are
    camelCase (``visualReview``, ``releaseReadiness``), not the model's
    Python field names -- checking the snake_case names here was a bug found
    live (2026-09-26): every receipt failed this gate's own shape check
    before ever reading the actual advisory status.

    This is a receipt-content check only -- it does not join the
    single-flight export identity authority (``services/public_export_identity.py``);
    that wiring is N1's (native-objects, Phase 2) to add once svg exports
    start using it. Until c1-quality lands, ``visualReview.status`` is
    hard-coded ``"failed"``, so release-v1 stays correctly rejected end to
    end -- that is expected, not a bug in this check.
    """
    from services.workspace_operation_handler_registry import NonRetryableWorkspaceOperationError
    visual_review = receipt.get("visualReview")
    if not isinstance(visual_review, dict):
        raise NonRetryableWorkspaceOperationError("svg_release_quality_receipt_invalid")
    if visual_review.get("status") != "passed":
        raise NonRetryableWorkspaceOperationError("svg_release_quality_visual_review_not_passed")
    evidence = visual_review.get("evidence")
    if not isinstance(evidence, dict) or not evidence.get("advisoryDigest"):
        raise NonRetryableWorkspaceOperationError("svg_release_quality_advisory_evidence_missing")
    if receipt.get("releaseReadiness") == "blocked":
        raise NonRetryableWorkspaceOperationError("svg_release_quality_not_release_ready")


__all__ = ["require_release_v1_receipt"]
