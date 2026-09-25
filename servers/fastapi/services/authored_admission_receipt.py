"""Digest-bound admission receipts and attempt-local capture reuse."""

from __future__ import annotations

from services.authored_admission_policy import (
    CAPTURE_MISSING,
    CONTEXT_MISMATCH,
    FLAG_ONLY_INELIGIBLE,
    HTML_DIGEST_MISMATCH,
    METRICS_DIGEST_MISMATCH,
    PERSISTENCE_CLASS_INVALID,
    PNG_DIGEST_MISMATCH,
    POLICY_REVISION_MISMATCH,
    RECEIPT_MISSING,
    RECEIPT_REJECTED,
    REPRESENTATION_INVALID,
)

from dataclasses import dataclass, replace
from typing import Any, Mapping

from services.authored_admission_context import AdmissionContext, canonical_json, digest_bytes, digest_html
from services.authored_rollout_profile import matches_policy_revision


#: A receipt minted from a live admission session carries the deck's real
#: identity: presentation, operation and source-unit ids, the manifest and
#: projection digests, and whether the deck reflows its source verbatim.
COMPLETE_IDENTITY = "complete"
#: A receipt minted at a checkpoint writer that had no session carries none of
#: that.  It is enough to admit the write -- the bytes were judged by a real
#: policy -- and never enough to publish one, because the fields a publication
#: reader would check were never observed, only defaulted.
INCOMPLETE_IDENTITY = "incomplete"
#: Raised instead of minting an incomplete-identity receipt for a deck whose
#: HTML carries source bindings: a verbatim deck's guarantee is the rendered
#: readback against its manifest, and there is no manifest without a session.
SESSION_IDENTITY_REQUIRED = "authored_admission_session_identity_required"


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    context: AdmissionContext
    html_digest: str
    verdict: str
    code: str
    predicate: str
    block_ids: tuple[str, ...] = ()
    metrics_digest: str | None = None
    png_digest: str | None = None
    capture_reused: bool = False
    capture_count: int = 0
    persistence_class: str = "publishable"
    normalizations: tuple[dict[str, Any], ...] = ()
    #: Whether the context above was observed or defaulted.  See the two
    #: identity constants: only ``COMPLETE_IDENTITY`` may publish.
    identity: str = COMPLETE_IDENTITY

    def as_dict(self) -> dict[str, Any]:
        context = self.context
        return {
            "schemaVersion": 2,
            "htmlRepr": context.html_repr,
            "presentationId": context.presentation_id,
            "operationId": context.operation_id,
            "slideIndex": context.slide_index,
            "sourceUnitId": context.source_unit_id,
            "manifestDigest": context.manifest_digest,
            "projectionDigest": context.projection_digest,
            "policyRevision": context.policy_revision,
            "fencedRevision": context.fenced_revision,
            # Attempt tokens belong in private receipts, never shadow log lines.
            "attemptToken": context.attempt_token,
            "contextDigest": context.identity_digest,
            "htmlDigest": self.html_digest,
            "verdict": self.verdict,
            "code": self.code,
            "predicate": self.predicate,
            "blockIds": list(self.block_ids[:32]),
            "blockCount": len(self.block_ids),
            "metricsDigest": self.metrics_digest,
            "pngDigest": self.png_digest,
            "metricsStorage": "checkpoint_bundle" if context.html_repr == "captured" else None,
            "captureProfile": context.capture_profile,
            "captureReused": self.capture_reused,
            "captureCount": self.capture_count,
            "persistenceClass": self.persistence_class,
            "identity": self.identity,
            "publishable": (
                self.persistence_class == "publishable"
                and self.verdict != "rejected"
                and self.identity == COMPLETE_IDENTITY
            ),
            "normalizations": list(self.normalizations[:32]),
            "normalizationCount": len(self.normalizations),
            "normalizationDigest": digest_html(canonical_json(self.normalizations)),
        }


class AdmissionReceiptError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require_admission_receipt(
    receipt: AdmissionReceipt | None,
    *,
    html: str,
    stage: str,
    slide_index: int,
    attempt_token: str,
    fenced_revision: int,
    operation_id: str | None = None,
    context: AdmissionContext | None = None,
    metrics: Mapping[str, object] | None = None,
    png: bytes | None = None,
) -> AdmissionReceipt:
    """Validate before any checkpoint artifact or DB mutation occurs."""
    if not isinstance(receipt, AdmissionReceipt):
        raise AdmissionReceiptError(RECEIPT_MISSING)
    pinned = receipt.context
    # The bytes were judged by one policy under one set of rollout switches.  A
    # writer running a different revision is looking at evidence from another
    # run, not at this one's, so it is refused rather than trusted.
    if not matches_policy_revision(pinned.policy_revision):
        raise AdmissionReceiptError(POLICY_REVISION_MISMATCH)
    if receipt.verdict == "flag_only" and not (
        pinned.source_reflow and pinned.terminal_fallback_eligible and pinned.already_admitted_flags
    ):
        raise AdmissionReceiptError(FLAG_ONLY_INELIGIBLE)
    if receipt.persistence_class not in {"evidence", "publishable"}:
        raise AdmissionReceiptError(PERSISTENCE_CLASS_INVALID)
    if receipt.verdict not in {"admitted", "flag_only"} and not (
        receipt.persistence_class == "evidence" and receipt.context.html_repr == "captured"
    ):
        raise AdmissionReceiptError(RECEIPT_REJECTED)
    if receipt.html_digest != digest_html(html):
        raise AdmissionReceiptError(HTML_DIGEST_MISMATCH)
    if (
        pinned.fenced_revision != fenced_revision
        or pinned.attempt_token != attempt_token
        or pinned.slide_index != slide_index
        or (operation_id is not None and pinned.operation_id != operation_id)
        or (context is not None and pinned.identity_digest != context.identity_digest)
    ):
        raise AdmissionReceiptError(CONTEXT_MISMATCH)
    if (stage != "author" and pinned.html_repr == "token") or (stage == "render" and pinned.html_repr != "captured"):
        raise AdmissionReceiptError(REPRESENTATION_INVALID)
    if stage == "render" and (metrics is None or png is None):
        raise AdmissionReceiptError(CAPTURE_MISSING)
    if metrics is not None and receipt.metrics_digest != digest_html(canonical_json(metrics)):
        raise AdmissionReceiptError(METRICS_DIGEST_MISMATCH)
    if png is not None and receipt.png_digest != digest_bytes(png):
        raise AdmissionReceiptError(PNG_DIGEST_MISMATCH)
    return receipt


@dataclass(frozen=True, slots=True)
class CapturedEvidence:
    receipt: AdmissionReceipt
    metrics_json: str
    png: bytes | None


class AdmissionCaptureStore:
    """One attempt owns this store; there is no process-global admission cache."""

    def __init__(self) -> None:
        self._captures: dict[tuple[str, str], CapturedEvidence] = {}

    def remember(self, receipt: AdmissionReceipt, metrics: Mapping[str, object], png: bytes | None) -> None:
        if receipt.context.html_repr != "captured" or receipt.verdict == "rejected":
            return
        key = (receipt.html_digest, receipt.context.capture_profile)
        self._captures[key] = CapturedEvidence(receipt, canonical_json(metrics), png)

    def reuse(self, context: AdmissionContext, html: str) -> CapturedEvidence | None:
        cached = self._captures.get((digest_html(html), context.capture_profile))
        if cached is None or cached.receipt.context.identity_digest != context.identity_digest:
            return None
        return replace(cached, receipt=replace(cached.receipt, capture_reused=True))
