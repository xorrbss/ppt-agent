"""Receipt status predicates and checkpoint reuse for authored local-fit.

Extracted from ``authored_local_fit_evidence``. These read a persisted
receipt back (rather than build one): classifying it as a passed quality
review vs. a preserved-original delivery, and deciding whether an existing
receipt is still valid to reuse for a checkpoint without re-rendering.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from services.authored_local_fit_evidence_runs import _policy_revision
from services.authored_local_fit_evidence_types import (
    ORIGINAL_DELIVERY_REVISION,
    RECEIPT_KEY,
    RECEIPT_SCHEMA,
)
from services.authored_local_fit_policy import (
    acceptance_key,
    baseline_digest,
    digest,
    html_digest,
)


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
    if (preserved_original or receipt.get("renderedFitDisabled") is True) and (
        final_png is None
        or receipt.get("png_digest")
        != "sha256:" + hashlib.sha256(final_png).hexdigest()
    ):
        return None
    fit_mode = (
        False if preserved_original or receipt.get("renderedFitDisabled") is True
        else receipt.get("changed") is not True
    )
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


__all__ = [
    "receipt_is_preserved_original",
    "receipt_quality_passed",
    "receipt_quality_is_publishable",
    "reusable_checkpoint_receipt",
]
