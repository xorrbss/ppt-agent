"""Quality review and immutable export workspace operations."""

from __future__ import annotations

from services.workspace_quality_receipt_digest import (
    _is_sha256_digest as _is_sha256_digest,
    _release_review_receipt_digest as _release_review_receipt_digest,
)

import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from models.sql.public_admission import PublicOperationAdmission
from services.authored_evidence_ledger import digest_deck_htmls
from services.authored_composition_policy import (
    CompositionProfile,
    blocking_safety_issues,
    build_authored_capacity_plan,
    capacity_violations,
)
from services.authored_quality_receipt_v2 import (
    validate_current_quality_receipt_payload,
    validate_quality_receipt_v3_payload,
)
from services.authored_quality_review_service import run_authored_quality_review
from services.authored_quality_telemetry import style_comparison_observation_metadata
from services.authored_post_edit_evidence import (
    AuthoredPostEditEvidenceError,
    refresh_authored_post_edit_evidence,
)
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload
from services.provider_usage_instrumentation import public_operation_scope
from services.public_quality_receipt_v3_store import append_public_review_evidence
from services.public_export_operation import (
    PublicExportOperationError,
    public_release_review_deck_render_identity_digest,
    public_release_review_runtime_binding,
    public_render_runtime_binding_digest,
)
from utils.authored_dom_quality import (
    inspect_authored_dom,
    validate_declared_authored_qids,
)
from utils.slide_capture import render_html_to_png_with_metrics
from utils.slide_capture import (
    CACHE_IDENTITY_VERSION,
    DOM_METRICS_VERSION,
    MEASUREMENT_CACHE_VERSION,
    OCCLUSION_SENSOR_VERSION,
)

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
# The review writes its slide fixes on the session it holds open until the final
# revision bump, so its lease must cover the whole pass rather than one renewal.
_LEASE_BASE_SECONDS = 600
_LEASE_PER_SLIDE_SECONDS = 60
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS = 30.0
_RELEASE_REVIEW_RECEIPT_SCHEMA = "export-release-review-receipt/v2"
_RELEASE_REVIEW_BASE_BINDING_SCHEMA = "export-release-review-base-binding/v2"
_RELEASE_REVIEW_BASE_TASK_KEY = "_workspace_release_review_base"
_RELEASE_REVIEW_CHECKER_POLICY_SCHEMA = "public-release-review-checker-policy/v1"
_RELEASE_REVIEW_IMPLEMENTATION_VERSION = "release-review-h0-h1/v2"
_RELEASE_REVIEW_CAPACITY_POLICY_VERSION = "authored-capacity-policy/v1"
_RELEASE_REVIEW_QID_POLICY_VERSION = "declared-authored-qids/v1"
_RELEASE_RECHECK_BASE_SCHEMA = "export-release-recheck-base/v1"
_BASE_EVIDENCE_GENERATION = "generation-quality-v3"
_BASE_EVIDENCE_REVIEW = "release-review-transition"
_BASE_EVIDENCE_RECOVERY = "release-recheck-transition"


def _requires_release_recheck_after_completed_task(
    *,
    release_policy: object,
    review_completed: bool,
    fixed_count: int,
) -> bool:
    """Never promote an already-committed mutating task directly to PASS.

    The task checkpoint is durable before presentation revision publication,
    but it does not seal the post-fix deck/render identity.  A resumed handler
    therefore has to publish only a recheck base even when its runtime and
    checker identities are unchanged.
    """

    return bool(
        release_policy == "release-v1"
        and review_completed
        and fixed_count > 0
    )


def _compatibility_review_runner():
    facade = sys.modules.get("services.workspace_quality_export_operation_handlers")
    return getattr(facade, "run_authored_quality_review", run_authored_quality_review)


def _compatibility_progress_interval() -> float:
    facade = sys.modules.get("services.workspace_quality_export_operation_handlers")
    return float(
        getattr(
            facade,
            "QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS",
            QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS,
        )
    )


def _context(claim_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = claim_payload.get("context")
    return value if isinstance(value, Mapping) else {}


def release_review_checker_policy() -> dict[str, Any]:
    """Return the code/config identity that can change a release PASS."""

    metadata = style_comparison_observation_metadata()
    return {
        "schemaVersion": _RELEASE_REVIEW_CHECKER_POLICY_SCHEMA,
        "implementationVersion": _RELEASE_REVIEW_IMPLEMENTATION_VERSION,
        "reviewModel": metadata["model"],
        "critiquePromptVersion": metadata["prompt_version"],
        "critiqueCacheVersion": metadata["cache_version"],
        "renderCacheIdentityVersion": CACHE_IDENTITY_VERSION,
        "measurementCacheVersion": MEASUREMENT_CACHE_VERSION,
        "domMetricsVersion": DOM_METRICS_VERSION,
        "occlusionSensorVersion": OCCLUSION_SENSOR_VERSION,
        "capacityPolicyVersion": _RELEASE_REVIEW_CAPACITY_POLICY_VERSION,
        "qidPolicyVersion": _RELEASE_REVIEW_QID_POLICY_VERSION,
    }


def release_review_checker_policy_digest() -> str:
    return _release_review_receipt_digest(release_review_checker_policy())


def _release_review_task_policy_matches(
    task_data: Mapping[str, Any], checker_policy: Mapping[str, Any]
) -> bool:
    """Require the exact pre-execution paired-review identity, including keys."""
    return task_data.get("review_policy") == {
        "model": checker_policy["reviewModel"],
        "prompt_version": checker_policy["critiquePromptVersion"],
        "cache_version": checker_policy["critiqueCacheVersion"],
    }


def validate_release_review_base_receipts(
    theme: object,
    *,
    expected_deck_html_digest: str | None = None,
    expected_quality_receipt_digest: str | None = None,
) -> tuple[str, str]:
    """Validate the additive V2/V3 chain and its exact persisted HTML identity."""

    if not isinstance(theme, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    gate = theme.get("authoredQualityGate")
    raw_v3 = gate.get("qualityReceiptV3") if isinstance(gate, Mapping) else None
    if not isinstance(gate, Mapping) or not isinstance(raw_v3, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    try:
        receipt_v2 = validate_current_quality_receipt_payload(gate)
        receipt_v3 = validate_quality_receipt_v3_payload(raw_v3)
    except ValueError as error:
        raise NonRetryableWorkspaceOperationError(
            "quality_receipt_v3_digest_invalid"
        ) from error
    if (
        not receipt_v2.gate_passed
        or receipt_v2.release_profile != "release"
        or receipt_v3.previous_schema_version != "quality-receipt/v2"
        or receipt_v3.previous_version_receipt_digest != receipt_v2.receipt_digest
        or (
            expected_deck_html_digest is not None
            and receipt_v2.deck_html_digest != expected_deck_html_digest
        )
        or (
            expected_quality_receipt_digest is not None
            and receipt_v3.receipt_digest != expected_quality_receipt_digest
        )
    ):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_blocked")
    unresolved = set(receipt_v3.blockers) - {"visualReview.required_failed"}
    if unresolved:
        raise NonRetryableWorkspaceOperationError(
            "quality_receipt_v3_blocked",
            detail={"unresolved_blockers": sorted(unresolved)},
        )
    return (
        receipt_v3.receipt_digest.removeprefix("sha256:"),
        receipt_v2.deck_html_digest,
    )


def _build_release_review_base_binding(
    *,
    operation_id: uuid.UUID,
    source_revision: int,
    deck_html_digest: str,
    quality_receipt_digest: str,
    render_runtime_binding_digest: str,
    source_render_identity_digest: str,
    checker_policy_digest: str,
    base_evidence_kind: str,
    base_evidence_operation_id: str | None,
    source_readback_reissue_required: bool,
) -> dict[str, Any]:
    sealed = {
        "schemaVersion": _RELEASE_REVIEW_BASE_BINDING_SCHEMA,
        "operationId": str(operation_id),
        "sourceRevision": source_revision,
        "deckHtmlDigest": deck_html_digest,
        "qualityReceiptDigest": quality_receipt_digest,
        "renderRuntimeBindingDigest": render_runtime_binding_digest,
        "sourceRenderIdentityDigest": source_render_identity_digest,
        "checkerPolicyDigest": checker_policy_digest,
        "baseEvidenceKind": base_evidence_kind,
        "baseEvidenceOperationId": base_evidence_operation_id,
        "sourceReadbackReissueRequired": source_readback_reissue_required,
    }
    return {**sealed, "bindingDigest": _release_review_receipt_digest(sealed)}


def _validate_release_review_base_binding(
    value: object,
    *,
    operation_id: uuid.UUID,
    source_revision: int,
    render_runtime_binding_digest: str,
    source_render_identity_digest: str,
    checker_policy_digest: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_base_missing"
        )
    sealed = {
        "schemaVersion": value.get("schemaVersion"),
        "operationId": value.get("operationId"),
        "sourceRevision": value.get("sourceRevision"),
        "deckHtmlDigest": value.get("deckHtmlDigest"),
        "qualityReceiptDigest": value.get("qualityReceiptDigest"),
        "renderRuntimeBindingDigest": value.get("renderRuntimeBindingDigest"),
        "sourceRenderIdentityDigest": value.get("sourceRenderIdentityDigest"),
        "checkerPolicyDigest": value.get("checkerPolicyDigest"),
        "baseEvidenceKind": value.get("baseEvidenceKind"),
        "baseEvidenceOperationId": value.get("baseEvidenceOperationId"),
        "sourceReadbackReissueRequired": value.get(
            "sourceReadbackReissueRequired"
        ),
    }
    if (
        sealed["schemaVersion"] != _RELEASE_REVIEW_BASE_BINDING_SCHEMA
        or sealed["operationId"] != str(operation_id)
        or sealed["sourceRevision"] != source_revision
        or not _is_sha256_digest(sealed["deckHtmlDigest"])
        or not _is_sha256_digest(sealed["qualityReceiptDigest"])
        or sealed["renderRuntimeBindingDigest"]
        != render_runtime_binding_digest
        or sealed["sourceRenderIdentityDigest"]
        != source_render_identity_digest
        or sealed["checkerPolicyDigest"] != checker_policy_digest
        or not _is_sha256_digest(sealed["renderRuntimeBindingDigest"])
        or not _is_sha256_digest(sealed["sourceRenderIdentityDigest"])
        or not _is_sha256_digest(sealed["checkerPolicyDigest"])
        or sealed["baseEvidenceKind"]
        not in {
            _BASE_EVIDENCE_GENERATION,
            _BASE_EVIDENCE_REVIEW,
            _BASE_EVIDENCE_RECOVERY,
        }
        or (
            sealed["baseEvidenceKind"] == _BASE_EVIDENCE_GENERATION
            and sealed["baseEvidenceOperationId"] is not None
        )
        or (
            sealed["baseEvidenceKind"] != _BASE_EVIDENCE_GENERATION
            and not isinstance(sealed["baseEvidenceOperationId"], str)
        )
        or not isinstance(sealed["sourceReadbackReissueRequired"], bool)
        or value.get("bindingDigest") != _release_review_receipt_digest(sealed)
    ):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_base_invalid"
        )
    return value


async def _evaluate_post_review_release_gate(
    *,
    presentation: PresentationModel,
    slides: list[SlideModel],
    htmls: list[str],
    base_deck_html_digest: str,
    render_runtime_binding_digest: str,
    source_render_identity_digest: str,
    checker_policy_digest: str,
    source_readback_reissue_required: bool,
    fixed_count: int,
) -> dict[str, Any]:
    """Re-check deterministic H1 safety after a review mutates H0 HTML.

    V2/V3 generation receipts remain useful provenance for the pre-review
    deck, but they cannot certify HTML changed by the review. Ordinary authored
    decks can reconstruct their frozen role capacity and browser safety gates;
    source-reflow decks fail closed after a fix until their source readback can
    be reissued for the new HTML.
    """

    result_deck_html_digest = digest_deck_htmls(htmls)
    gate = (
        presentation.theme.get("authoredQualityGate")
        if isinstance(presentation.theme, Mapping)
        else None
    )
    profile_id = gate.get("profileId") if isinstance(gate, Mapping) else None
    issues: list[dict[str, Any]] = []
    if source_readback_reissue_required:
        issues.append({"code": "source_readback_reissue_required"})
    elif fixed_count == 0:
        if result_deck_html_digest != base_deck_html_digest:
            issues.append({"code": "unchanged_review_html_drift"})
    elif profile_id != "ordinary":
        issues.append({"code": "source_readback_reissue_required"})
    else:
        outline = presentation.get_presentation_outline()
        outline_slides = list(outline.slides) if outline is not None else []
        if len(outline_slides) != len(slides):
            issues.append({"code": "outline_slide_count_mismatch"})
        else:
            contents = [str(item.content or "") for item in outline_slides]
            roles = [
                str(
                    (slide.content or {}).get("role")
                    if isinstance(slide.content, Mapping)
                    else ""
                )
                or str(slide.layout or "").rsplit(":", 1)[-1]
                for slide in slides
            ]
            profile = CompositionProfile(
                profile_id="ordinary",
                text_mode="authored",
                allow_summarization=True,
                strict_dom_gate=True,
                max_repair_cycles=1,
                quality_profile_id="release",
            )
            capacity_plan = build_authored_capacity_plan(contents, roles, profile)
            capture_gate = asyncio.Semaphore(2)

            async def capture_metrics(html: str):
                async with capture_gate:
                    return await render_html_to_png_with_metrics(html)

            captures = await asyncio.gather(
                *(capture_metrics(html) for html in htmls)
            )
            for index, (html, capture) in enumerate(
                zip(htmls, captures, strict=True)
            ):
                metrics = (
                    capture.browser_metrics
                    if hasattr(capture, "browser_metrics")
                    else capture[1]
                )
                slide_issues: list[str] = list(
                    capacity_violations(html, metrics, capacity_plan.at(index))
                )
                report = inspect_authored_dom(
                    html,
                    metrics,
                    reference_mode="style_reference",
                    strict_structural_off_canvas=True,
                )
                if report.telemetry_status != "available":
                    slide_issues.append(
                        f"telemetry:{report.telemetry_status}"
                    )
                slide_issues.extend(
                    f"safety:{issue.category}"
                    for issue in blocking_safety_issues(report)
                )
                try:
                    validate_declared_authored_qids(html)
                except ValueError:
                    slide_issues.append("qid:invalid")
                if slide_issues:
                    issues.append(
                        {
                            "slideIndex": index,
                            "codes": sorted(set(slide_issues)),
                        }
                    )
    try:
        current_runtime_binding = public_release_review_runtime_binding()
        current_runtime_binding_digest = public_render_runtime_binding_digest(
            current_runtime_binding
        )
        result_render_identity_digest = (
            public_release_review_deck_render_identity_digest(htmls)
        )
    except PublicExportOperationError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    if current_runtime_binding_digest != render_runtime_binding_digest:
        issues.append({"code": "review_runtime_binding_changed"})
    current_checker_policy_digest = release_review_checker_policy_digest()
    if current_checker_policy_digest != checker_policy_digest:
        issues.append({"code": "review_checker_policy_changed"})
    if fixed_count == 0 and (
        result_render_identity_digest != source_render_identity_digest
    ):
        issues.append({"code": "review_render_identity_changed"})
    evidence = {
        "schemaVersion": "export-release-post-review-gate/v2",
        "profileId": str(profile_id or "missing"),
        "baseDeckHtmlDigest": base_deck_html_digest,
        "resultDeckHtmlDigest": result_deck_html_digest,
        "renderRuntimeBindingDigest": current_runtime_binding_digest,
        "sourceRenderIdentityDigest": source_render_identity_digest,
        "resultRenderIdentityDigest": result_render_identity_digest,
        "checkerPolicyDigest": current_checker_policy_digest,
        "fixedCount": fixed_count,
        "slideCount": len(htmls),
        "issues": issues,
        "gatePassed": not issues,
    }
    return {
        **evidence,
        "evidenceDigest": _release_review_receipt_digest(evidence),
    }


def build_export_release_review_receipt(
    *,
    operation_id: uuid.UUID,
    source_revision: int,
    result_revision: int,
    slide_count: int,
    review_result: Mapping[str, Any],
    admission: PublicOperationAdmission,
    base_deck_html_digest: str,
    base_quality_receipt_digest: str,
    result_deck_html_digest: str,
    render_runtime_binding_digest: str,
    source_render_identity_digest: str,
    result_render_identity_digest: str,
    checker_policy_digest: str,
    base_evidence_kind: str,
    base_evidence_operation_id: str | None,
    post_review_gate: Mapping[str, Any],
    evidence_sink: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal measured full-deck review evidence without manufacturing a PASS."""

    def count(name: str) -> int:
        value = review_result.get(name, 0)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else -1

    reviewed_count = count("reviewed_count")
    remaining_issue_count = count("remaining_issue_count")
    incomplete_count = count("incomplete_measurement_count")
    unresolved_count = count("unresolved_confirmed_issue_count")
    fixed_count = count("fixed_count")
    slide_results = review_result.get("slide_results")
    slide_indices: list[int] = []
    slide_results_valid = isinstance(slide_results, list)
    if slide_results_valid:
        for item in slide_results:
            if not isinstance(item, Mapping):
                slide_results_valid = False
                break
            slide_index = item.get("slide_index")
            if (
                isinstance(slide_index, bool)
                or not isinstance(slide_index, int)
                or slide_index < 0
            ):
                slide_results_valid = False
                break
            slide_indices.append(slide_index)
    slide_results_valid = bool(
        slide_results_valid
        and len(slide_indices) == slide_count
        and sorted(slide_indices) == list(range(slide_count))
    )
    post_review_gate_digest = post_review_gate.get("evidenceDigest")
    post_review_gate_valid = bool(
        post_review_gate.get("schemaVersion")
        == "export-release-post-review-gate/v2"
        and post_review_gate.get("gatePassed") is True
        and post_review_gate.get("baseDeckHtmlDigest")
        == base_deck_html_digest
        and post_review_gate.get("resultDeckHtmlDigest")
        == result_deck_html_digest
        and post_review_gate.get("renderRuntimeBindingDigest")
        == render_runtime_binding_digest
        and post_review_gate.get("sourceRenderIdentityDigest")
        == source_render_identity_digest
        and post_review_gate.get("resultRenderIdentityDigest")
        == result_render_identity_digest
        and post_review_gate.get("checkerPolicyDigest")
        == checker_policy_digest
        and _is_sha256_digest(post_review_gate_digest)
        and post_review_gate_digest
        == _release_review_receipt_digest(
            {
                key: value
                for key, value in post_review_gate.items()
                if key != "evidenceDigest"
            }
        )
    )
    gate_passed = bool(
        reviewed_count == slide_count
        and slide_count > 0
        and remaining_issue_count == 0
        and incomplete_count == 0
        and unresolved_count == 0
        and fixed_count >= 0
        and slide_results_valid
        and _is_sha256_digest(base_deck_html_digest)
        and _is_sha256_digest(base_quality_receipt_digest)
        and _is_sha256_digest(result_deck_html_digest)
        and _is_sha256_digest(render_runtime_binding_digest)
        and _is_sha256_digest(source_render_identity_digest)
        and _is_sha256_digest(result_render_identity_digest)
        and _is_sha256_digest(checker_policy_digest)
        and base_evidence_kind
        in {
            _BASE_EVIDENCE_GENERATION,
            _BASE_EVIDENCE_REVIEW,
            _BASE_EVIDENCE_RECOVERY,
        }
        and (
            base_evidence_operation_id is None
            if base_evidence_kind == _BASE_EVIDENCE_GENERATION
            else isinstance(base_evidence_operation_id, str)
        )
        and post_review_gate_valid
    )
    evidence = {
        "reviewedCount": reviewed_count,
        "slideCount": slide_count,
        "remainingIssueCount": remaining_issue_count,
        "incompleteMeasurementCount": incomplete_count,
        "unresolvedConfirmedIssueCount": unresolved_count,
        "fixedCount": fixed_count,
        "slideResultsDigest": _release_review_receipt_digest(
            {"slideResults": list(slide_results) if slide_results_valid else []}
        ),
        "providerAuthority": {
            "admissionId": str(admission.id),
            "plannedRequestLimit": int(admission.planned_request_limit),
            "retryRequestLimit": int(admission.retry_request_limit),
            "initialRequestCount": int(admission.initial_request_count),
            "retryRequestCount": int(admission.retry_request_count),
            "estimatedConsumedMicrounits": int(
                admission.estimated_consumed_microunits
            ),
            "actualConsumedMicrounits": int(admission.actual_consumed_microunits),
            "actualCostUnavailableCount": int(
                admission.actual_cost_unavailable_count
            ),
        },
        "postReviewGateDigest": post_review_gate_digest,
        "renderRuntimeBindingDigest": render_runtime_binding_digest,
        "sourceRenderIdentityDigest": source_render_identity_digest,
        "resultRenderIdentityDigest": result_render_identity_digest,
        "checkerPolicyDigest": checker_policy_digest,
        "baseEvidenceKind": base_evidence_kind,
        "baseEvidenceOperationId": base_evidence_operation_id,
    }
    sealed = {
        "schemaVersion": _RELEASE_REVIEW_RECEIPT_SCHEMA,
        "operationId": str(operation_id),
        "sourceRevision": source_revision,
        "resultRevision": result_revision,
        "baseDeckHtmlDigest": base_deck_html_digest,
        "baseQualityReceiptDigest": base_quality_receipt_digest,
        "resultDeckHtmlDigest": result_deck_html_digest,
        "renderRuntimeBindingDigest": render_runtime_binding_digest,
        "sourceRenderIdentityDigest": source_render_identity_digest,
        "resultRenderIdentityDigest": result_render_identity_digest,
        "checkerPolicyDigest": checker_policy_digest,
        "baseEvidenceKind": base_evidence_kind,
        "baseEvidenceOperationId": base_evidence_operation_id,
        "postReviewGateDigest": post_review_gate_digest,
        "evidenceDigest": _release_review_receipt_digest(evidence),
        "gatePassed": gate_passed,
    }
    if evidence_sink is not None:
        evidence_sink.update({
            "checkerEvidence": evidence,
            "postReviewGate": dict(post_review_gate),
            "slideResults": list(slide_results) if slide_results_valid else [],
        })
    return {**sealed, "receiptDigest": _release_review_receipt_digest(sealed)}


def validate_export_release_review_receipt(
    receipt: object,
    *,
    operation_id: uuid.UUID,
    source_revision: int,
    result_revision: int,
) -> str:
    """Return the verified digest for a passed, revision-bound review."""

    if not isinstance(receipt, Mapping):
        raise NonRetryableWorkspaceOperationError("release_quality_review_required")
    sealed = {
        "schemaVersion": receipt.get("schemaVersion"),
        "operationId": receipt.get("operationId"),
        "sourceRevision": receipt.get("sourceRevision"),
        "resultRevision": receipt.get("resultRevision"),
        "baseDeckHtmlDigest": receipt.get("baseDeckHtmlDigest"),
        "baseQualityReceiptDigest": receipt.get("baseQualityReceiptDigest"),
        "resultDeckHtmlDigest": receipt.get("resultDeckHtmlDigest"),
        "renderRuntimeBindingDigest": receipt.get(
            "renderRuntimeBindingDigest"
        ),
        "sourceRenderIdentityDigest": receipt.get(
            "sourceRenderIdentityDigest"
        ),
        "resultRenderIdentityDigest": receipt.get(
            "resultRenderIdentityDigest"
        ),
        "checkerPolicyDigest": receipt.get("checkerPolicyDigest"),
        "baseEvidenceKind": receipt.get("baseEvidenceKind"),
        "baseEvidenceOperationId": receipt.get("baseEvidenceOperationId"),
        "postReviewGateDigest": receipt.get("postReviewGateDigest"),
        "evidenceDigest": receipt.get("evidenceDigest"),
        "gatePassed": receipt.get("gatePassed"),
    }
    if (
        sealed["schemaVersion"] != _RELEASE_REVIEW_RECEIPT_SCHEMA
        or sealed["operationId"] != str(operation_id)
        or sealed["sourceRevision"] != source_revision
        or sealed["resultRevision"] != result_revision
        or sealed["gatePassed"] is not True
        or not _is_sha256_digest(sealed["baseDeckHtmlDigest"])
        or not _is_sha256_digest(sealed["baseQualityReceiptDigest"])
        or not _is_sha256_digest(sealed["resultDeckHtmlDigest"])
        or not _is_sha256_digest(sealed["renderRuntimeBindingDigest"])
        or not _is_sha256_digest(sealed["sourceRenderIdentityDigest"])
        or not _is_sha256_digest(sealed["resultRenderIdentityDigest"])
        or not _is_sha256_digest(sealed["checkerPolicyDigest"])
        or sealed["baseEvidenceKind"]
        not in {
            _BASE_EVIDENCE_GENERATION,
            _BASE_EVIDENCE_REVIEW,
            _BASE_EVIDENCE_RECOVERY,
        }
        or (
            sealed["baseEvidenceKind"] == _BASE_EVIDENCE_GENERATION
            and sealed["baseEvidenceOperationId"] is not None
        )
        or (
            sealed["baseEvidenceKind"] != _BASE_EVIDENCE_GENERATION
            and not isinstance(sealed["baseEvidenceOperationId"], str)
        )
        or not _is_sha256_digest(sealed["postReviewGateDigest"])
        or not isinstance(sealed["evidenceDigest"], str)
        or not sealed["evidenceDigest"].startswith("sha256:")
        or receipt.get("receiptDigest") != _release_review_receipt_digest(sealed)
    ):
        raise NonRetryableWorkspaceOperationError("release_quality_review_blocked")
    return str(receipt["receiptDigest"]).removeprefix("sha256:")


def _build_release_recheck_base(
    *,
    operation_id: uuid.UUID,
    source_revision: int,
    result_revision: int,
    base_binding: Mapping[str, Any],
    result_deck_html_digest: str,
    fixed_count: int,
    source_readback_reissue_required: bool,
) -> dict[str, Any]:
    """Seal a crash-recovery transition without claiming a quality PASS."""

    sealed = {
        "schemaVersion": _RELEASE_RECHECK_BASE_SCHEMA,
        "operationId": str(operation_id),
        "sourceRevision": source_revision,
        "resultRevision": result_revision,
        "baseDeckHtmlDigest": base_binding.get("deckHtmlDigest"),
        "baseQualityReceiptDigest": base_binding.get("qualityReceiptDigest"),
        "resultDeckHtmlDigest": result_deck_html_digest,
        "renderRuntimeBindingDigest": base_binding.get(
            "renderRuntimeBindingDigest"
        ),
        "sourceRenderIdentityDigest": base_binding.get(
            "sourceRenderIdentityDigest"
        ),
        "checkerPolicyDigest": base_binding.get("checkerPolicyDigest"),
        "fixedCount": fixed_count,
        "sourceReadbackReissueRequired": source_readback_reissue_required,
        "recheckRequired": True,
    }
    return {**sealed, "receiptDigest": _release_review_receipt_digest(sealed)}


def _validate_release_recheck_base(
    value: object,
    *,
    operation_id: uuid.UUID,
    source_revision: int,
    result_revision: int,
) -> str:
    if not isinstance(value, Mapping):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_recheck_base_invalid"
        )
    sealed = {
        "schemaVersion": value.get("schemaVersion"),
        "operationId": value.get("operationId"),
        "sourceRevision": value.get("sourceRevision"),
        "resultRevision": value.get("resultRevision"),
        "baseDeckHtmlDigest": value.get("baseDeckHtmlDigest"),
        "baseQualityReceiptDigest": value.get("baseQualityReceiptDigest"),
        "resultDeckHtmlDigest": value.get("resultDeckHtmlDigest"),
        "renderRuntimeBindingDigest": value.get("renderRuntimeBindingDigest"),
        "sourceRenderIdentityDigest": value.get("sourceRenderIdentityDigest"),
        "checkerPolicyDigest": value.get("checkerPolicyDigest"),
        "fixedCount": value.get("fixedCount"),
        "sourceReadbackReissueRequired": value.get(
            "sourceReadbackReissueRequired"
        ),
        "recheckRequired": value.get("recheckRequired"),
    }
    if (
        sealed["schemaVersion"] != _RELEASE_RECHECK_BASE_SCHEMA
        or sealed["operationId"] != str(operation_id)
        or sealed["sourceRevision"] != source_revision
        or sealed["resultRevision"] != result_revision
        or any(
            not _is_sha256_digest(sealed[key])
            for key in (
                "baseDeckHtmlDigest",
                "baseQualityReceiptDigest",
                "resultDeckHtmlDigest",
                "renderRuntimeBindingDigest",
                "sourceRenderIdentityDigest",
                "checkerPolicyDigest",
            )
        )
        or not isinstance(sealed["fixedCount"], int)
        or isinstance(sealed["fixedCount"], bool)
        or sealed["fixedCount"] <= 0
        or not isinstance(sealed["sourceReadbackReissueRequired"], bool)
        or sealed["recheckRequired"] is not True
        or value.get("receiptDigest")
        != _release_review_receipt_digest(sealed)
    ):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_recheck_base_invalid"
        )
    return str(value["receiptDigest"])


async def _resolve_release_review_base_evidence(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    owner_scope: str,
    source_revision: int,
    source_deck_html_digest: str,
) -> dict[str, Any]:
    """Resolve generation evidence or a sealed prior-review transition."""

    generation_error: NonRetryableWorkspaceOperationError | None = None
    try:
        quality_digest, bound_deck_digest = validate_release_review_base_receipts(
            presentation.theme,
            expected_deck_html_digest=source_deck_html_digest,
        )
        return {
            "baseEvidenceKind": _BASE_EVIDENCE_GENERATION,
            "baseEvidenceOperationId": None,
            "deckHtmlDigest": bound_deck_digest,
            "qualityReceiptDigest": "sha256:" + quality_digest,
            "sourceReadbackReissueRequired": False,
        }
    except NonRetryableWorkspaceOperationError as error:
        generation_error = error

    statement = (
        select(WorkspaceOperation)
        .where(
            WorkspaceOperation.id != operation_id,
            WorkspaceOperation.presentation_id == presentation.id,
            WorkspaceOperation.owner_scope == owner_scope,
            WorkspaceOperation.kind == QUALITY_REVIEW_OPERATION_KIND,
            WorkspaceOperation.state == "succeeded",
            WorkspaceOperation.result_revision == source_revision,
        )
        .order_by(
            WorkspaceOperation.completed_at.desc(),
            WorkspaceOperation.created_at.desc(),
        )
        .limit(32)
    )
    if owner_id is not None:
        statement = statement.where(WorkspaceOperation.owner_id == owner_id)
    candidates = list((await session.scalars(statement)).all())
    for candidate in candidates:
        if not isinstance(candidate.result, Mapping):
            continue
        prior_receipt = candidate.result.get("release_review_receipt")
        if (
            isinstance(candidate.expected_revision, int)
            and isinstance(candidate.result_revision, int)
            and isinstance(prior_receipt, Mapping)
        ):
            try:
                validate_export_release_review_receipt(
                    prior_receipt,
                    operation_id=candidate.id,
                    source_revision=candidate.expected_revision,
                    result_revision=candidate.result_revision,
                )
            except NonRetryableWorkspaceOperationError:
                pass
            else:
                if (
                    prior_receipt.get("resultDeckHtmlDigest")
                    == source_deck_html_digest
                ):
                    return {
                        "baseEvidenceKind": _BASE_EVIDENCE_REVIEW,
                        "baseEvidenceOperationId": str(candidate.id),
                        "deckHtmlDigest": source_deck_html_digest,
                        "qualityReceiptDigest": prior_receipt.get(
                            "receiptDigest"
                        ),
                        "sourceReadbackReissueRequired": False,
                    }
        recovery = candidate.result.get("release_recheck_base")
        if (
            isinstance(candidate.expected_revision, int)
            and isinstance(candidate.result_revision, int)
            and isinstance(recovery, Mapping)
        ):
            try:
                recovery_digest = _validate_release_recheck_base(
                    recovery,
                    operation_id=candidate.id,
                    source_revision=candidate.expected_revision,
                    result_revision=candidate.result_revision,
                )
            except NonRetryableWorkspaceOperationError:
                continue
            if recovery.get("resultDeckHtmlDigest") == source_deck_html_digest:
                return {
                    "baseEvidenceKind": _BASE_EVIDENCE_RECOVERY,
                    "baseEvidenceOperationId": str(candidate.id),
                    "deckHtmlDigest": source_deck_html_digest,
                    "qualityReceiptDigest": recovery_digest,
                    "sourceReadbackReissueRequired": bool(
                        recovery.get("sourceReadbackReissueRequired")
                    ),
                }
    assert generation_error is not None
    raise generation_error


async def validate_release_review_base_evidence_for_export(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
    review_operation: WorkspaceOperation,
    receipt: Mapping[str, Any],
) -> tuple[str, str]:
    """Revalidate the immediate base transition of a passed review receipt."""

    base_kind = receipt.get("baseEvidenceKind")
    base_deck_digest = str(receipt.get("baseDeckHtmlDigest", ""))
    base_quality_digest = str(receipt.get("baseQualityReceiptDigest", ""))
    if base_kind == _BASE_EVIDENCE_GENERATION:
        return validate_release_review_base_receipts(
            presentation.theme,
            expected_deck_html_digest=base_deck_digest,
            expected_quality_receipt_digest=base_quality_digest,
        )
    try:
        base_operation_id = uuid.UUID(str(receipt.get("baseEvidenceOperationId")))
    except (TypeError, ValueError) as error:
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_base_invalid"
        ) from error
    base_operation = await session.get(WorkspaceOperation, base_operation_id)
    if (
        base_operation is None
        or base_operation.id == review_operation.id
        or base_operation.presentation_id != review_operation.presentation_id
        or base_operation.owner_id != review_operation.owner_id
        or base_operation.owner_scope != review_operation.owner_scope
        or base_operation.kind != QUALITY_REVIEW_OPERATION_KIND
        or base_operation.state != "succeeded"
        or base_operation.result_revision != review_operation.expected_revision
        or not isinstance(base_operation.expected_revision, int)
        or not isinstance(base_operation.result_revision, int)
        or not isinstance(base_operation.result, Mapping)
    ):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_base_invalid"
        )
    if base_kind == _BASE_EVIDENCE_REVIEW:
        base_receipt = base_operation.result.get("release_review_receipt")
        validate_export_release_review_receipt(
            base_receipt,
            operation_id=base_operation.id,
            source_revision=base_operation.expected_revision,
            result_revision=base_operation.result_revision,
        )
        if not isinstance(base_receipt, Mapping):
            raise NonRetryableWorkspaceOperationError(
                "release_quality_review_base_invalid"
            )
        actual_quality_digest = str(base_receipt.get("receiptDigest", ""))
        actual_deck_digest = str(base_receipt.get("resultDeckHtmlDigest", ""))
    elif base_kind == _BASE_EVIDENCE_RECOVERY:
        recovery = base_operation.result.get("release_recheck_base")
        actual_quality_digest = _validate_release_recheck_base(
            recovery,
            operation_id=base_operation.id,
            source_revision=base_operation.expected_revision,
            result_revision=base_operation.result_revision,
        )
        if not isinstance(recovery, Mapping):
            raise NonRetryableWorkspaceOperationError(
                "release_quality_review_base_invalid"
            )
        actual_deck_digest = str(recovery.get("resultDeckHtmlDigest", ""))
    else:
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_base_invalid"
        )
    if (
        actual_quality_digest != base_quality_digest
        or actual_deck_digest != base_deck_digest
    ):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_base_invalid"
        )
    return base_quality_digest.removeprefix("sha256:"), base_deck_digest


class WorkspaceQualityReviewOperationHandler:
    """Run authored review and publish a new revision only when fixes are saved."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def _published_result_after_commit_error(self, operation_id: uuid.UUID) -> WorkspaceOperationResult | None:
        """Reconcile an ambiguous commit acknowledgement without republishing."""

        async with self._session_factory() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            if (
                operation is None
                or operation.state != "succeeded"
                or not isinstance(operation.result, dict)
                or operation.result_revision is None
            ):
                return None
            return WorkspaceOperationResult(
                payload=operation.result,
                result_revision=operation.result_revision,
            )

    async def _run_review_with_live_progress(
        self,
        *,
        context: WorkspaceOperationExecutionContext,
        presentation_id: uuid.UUID,
        request: AuthoredQualityReviewRequest,
        task: AsyncPresentationGenerationTaskModel,
        session: AsyncSession,
    ) -> None:
        """Keep long reviews visibly alive and stop work after losing the lease."""

        review = asyncio.create_task(_compatibility_review_runner()(presentation_id, request, task, session))
        percent = 20
        try:
            while True:
                done, _pending = await asyncio.wait({review}, timeout=_compatibility_progress_interval())
                if review in done:
                    await review
                    return
                await context.raise_if_cancelled()
                # Approach 95% without claiming completion before results have
                # passed the revision and task-status checks below.
                percent = min(95, percent + max(1, (95 - percent + 7) // 8))
                progressed = await context.progress(stage="quality_review_analyzing", percent=percent)
                if progressed is False:
                    raise WorkspaceOperationLeaseLost("operation_lease_lost")
        finally:
            if not review.done():
                review.cancel()
                await asyncio.gather(review, return_exceptions=True)

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")
        command_context = _context(claim.request_payload)
        mode = command_context.get("mode", "analyze_and_fix")
        if mode != "analyze_and_fix":
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        scope = command_context.get("scope", "all")
        slide_indices = command_context.get("slide_indices", [])
        slidespec_opt_in = command_context.get("slidespec_opt_in", True)
        release_policy = command_context.get("release_policy")
        expected_runtime_binding_digest = command_context.get(
            "render_runtime_binding_digest"
        )
        expected_source_render_identity_digest = command_context.get(
            "source_render_identity_digest"
        )
        expected_checker_policy_digest = command_context.get(
            "checker_policy_digest"
        )
        if (
            scope not in {"all", "current", "subset"}
            or not isinstance(slide_indices, list)
            or not isinstance(slidespec_opt_in, bool)
            or release_policy not in {None, "release-v1"}
        ):
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        release_identity_changed = False
        release_identity_change_reason = "release_quality_review_identity_changed"
        if release_policy == "release-v1":
            if (
                not _is_sha256_digest(expected_runtime_binding_digest)
                or not _is_sha256_digest(expected_source_render_identity_digest)
                or not _is_sha256_digest(expected_checker_policy_digest)
            ):
                raise NonRetryableWorkspaceOperationError(
                    "release_quality_review_identity_required"
                )
            try:
                current_runtime_binding_digest = (
                    public_render_runtime_binding_digest(
                        public_release_review_runtime_binding()
                    )
                )
            except PublicExportOperationError as error:
                release_identity_changed = True
                release_identity_change_reason = str(error)
                current_runtime_binding_digest = None
            if (
                current_runtime_binding_digest is not None
                and current_runtime_binding_digest
                != expected_runtime_binding_digest
            ):
                release_identity_changed = True
                release_identity_change_reason = (
                    "release_quality_review_runtime_changed"
                )
            if release_review_checker_policy_digest() != expected_checker_policy_digest:
                release_identity_changed = True
                release_identity_change_reason = (
                    "release_quality_review_checker_changed"
                )
        try:
            request = AuthoredQualityReviewRequest(
                scope=scope,
                slide_indices=slide_indices,
                mode="analyze_and_fix",
                slidespec_opt_in=slidespec_opt_in,
            )
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid") from error

        await context.raise_if_cancelled()
        if await context.progress(stage="quality_review_preparing", percent=5) is False:
            raise WorkspaceOperationLeaseLost("operation_lease_lost")
        async with self._session_factory() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            inspected_revision = int(presentation.revision)
            if int(inspected_revision) != claim.expected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision",
                    detail={"current_revision": int(inspected_revision)},
                )
            public_admission = await session.scalar(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == claim.operation_id
                )
            )
            if release_policy == "release-v1" and (
                public_admission is None or public_admission.state != "active"
            ):
                raise NonRetryableWorkspaceOperationError(
                    "release_quality_review_admission_required"
                )
            # A stable task id is the durable resume marker for this operation.
            # Once slide fixes and the completed task result are committed
            # together, a worker retry can publish that result without running
            # the mutating review a second time.
            task_id = f"workspace-quality-{claim.operation_id}"
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            review_completed = task is not None and task.status == "completed" and isinstance(task.data, dict)
            if (
                release_policy == "release-v1"
                and release_identity_changed
                and not review_completed
            ):
                raise NonRetryableWorkspaceOperationError(
                    release_identity_change_reason
                )
            release_base_binding: Mapping[str, Any] | None = None
            if release_policy == "release-v1":
                assert isinstance(expected_runtime_binding_digest, str)
                assert isinstance(expected_source_render_identity_digest, str)
                assert isinstance(expected_checker_policy_digest, str)
                existing_binding = (
                    task.data.get(_RELEASE_REVIEW_BASE_TASK_KEY)
                    if task is not None and isinstance(task.data, Mapping)
                    else None
                )
                source_deck_html_digest: str | None = None
                if not review_completed:
                    slide_html_rows = await session.execute(
                        select(SlideModel.html_content)
                        .where(SlideModel.presentation == claim.presentation_id)
                        .order_by(SlideModel.index, SlideModel.id)
                    )
                    source_htmls = list(slide_html_rows.scalars())
                    if not source_htmls or any(
                        not isinstance(html, str) or not html
                        for html in source_htmls
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_authored_html_required"
                        )
                    try:
                        actual_source_render_identity_digest = (
                            public_release_review_deck_render_identity_digest(
                                cast(list[str], source_htmls)
                            )
                        )
                    except PublicExportOperationError as error:
                        raise NonRetryableWorkspaceOperationError(str(error)) from error
                    if (
                        actual_source_render_identity_digest
                        != expected_source_render_identity_digest
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_render_identity_changed"
                        )
                    source_deck_html_digest = digest_deck_htmls(
                        cast(list[str], source_htmls)
                    )
                    resolved_base_evidence = (
                        await _resolve_release_review_base_evidence(
                            session,
                            presentation=presentation,
                            operation_id=claim.operation_id,
                            owner_id=claim.owner_id,
                            owner_scope=claim.owner_scope,
                            source_revision=int(inspected_revision),
                            source_deck_html_digest=source_deck_html_digest,
                        )
                    )
                else:
                    resolved_base_evidence = None
                if existing_binding is None:
                    if review_completed:
                        # A completed mutating review may already have committed
                        # slide fixes. Never manufacture the pre-review identity
                        # from those post-review rows.
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_base_missing"
                        )
                    assert resolved_base_evidence is not None
                    release_base_binding = _build_release_review_base_binding(
                        operation_id=claim.operation_id,
                        source_revision=int(inspected_revision),
                        deck_html_digest=str(
                            resolved_base_evidence["deckHtmlDigest"]
                        ),
                        quality_receipt_digest=str(
                            resolved_base_evidence["qualityReceiptDigest"]
                        ),
                        render_runtime_binding_digest=(
                            expected_runtime_binding_digest
                        ),
                        source_render_identity_digest=(
                            expected_source_render_identity_digest
                        ),
                        checker_policy_digest=expected_checker_policy_digest,
                        base_evidence_kind=str(
                            resolved_base_evidence["baseEvidenceKind"]
                        ),
                        base_evidence_operation_id=(
                            str(resolved_base_evidence["baseEvidenceOperationId"])
                            if resolved_base_evidence["baseEvidenceOperationId"]
                            is not None
                            else None
                        ),
                        source_readback_reissue_required=bool(
                            resolved_base_evidence[
                                "sourceReadbackReissueRequired"
                            ]
                        ),
                    )
                else:
                    release_base_binding = _validate_release_review_base_binding(
                        existing_binding,
                        operation_id=claim.operation_id,
                        source_revision=int(inspected_revision),
                        render_runtime_binding_digest=(
                            expected_runtime_binding_digest
                        ),
                        source_render_identity_digest=(
                            expected_source_render_identity_digest
                        ),
                        checker_policy_digest=expected_checker_policy_digest,
                    )
                    if resolved_base_evidence is not None and any(
                        release_base_binding.get(key)
                        != resolved_base_evidence.get(key)
                        for key in (
                            "baseEvidenceKind",
                            "baseEvidenceOperationId",
                            "deckHtmlDigest",
                            "qualityReceiptDigest",
                            "sourceReadbackReissueRequired",
                        )
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_base_invalid"
                        )
            if task is None:
                task = AsyncPresentationGenerationTaskModel(
                    id=task_id,
                    status="pending",
                    message="Preparing quality review.",
                    data=(
                        {_RELEASE_REVIEW_BASE_TASK_KEY: dict(release_base_binding)}
                        if release_base_binding is not None
                        else None
                    ),
                )
                session.add(task)
                await session.commit()
            elif not review_completed:
                task.status = "pending"
                task.message = "Resuming quality review."
                task.data = (
                    {_RELEASE_REVIEW_BASE_TASK_KEY: dict(release_base_binding)}
                    if release_base_binding is not None
                    else None
                )
                task.error = None
                session.add(task)
                await session.commit()

            if not review_completed:
                # The review may commit its durable task checkpoint and slide
                # fixes before the presentation revision publication below.
                # Preserve the pre-review binding in that same task checkpoint
                # so a fenced retry never derives it from post-review HTML.
                reviewed_slides = len(slide_indices) if scope != "all" else (
                    await session.scalar(
                        select(func.count())
                        .select_from(SlideModel)
                        .where(SlideModel.presentation == claim.presentation_id)
                    )
                    or 0
                )
                await context.extend_lease(
                    seconds=_LEASE_BASE_SECONDS
                    + _LEASE_PER_SLIDE_SECONDS * int(reviewed_slides)
                )
                try:
                    if await context.progress(stage="quality_review_analyzing", percent=20) is False:
                        raise WorkspaceOperationLeaseLost("operation_lease_lost")
                    with public_operation_scope(
                        claim.operation_id if public_admission is not None else None,
                        attempt_token=(
                            claim.attempt_token if public_admission is not None else None
                        ),
                        fencing_token=(
                            claim.fencing_token if public_admission is not None else None
                        ),
                    ):
                        await self._run_review_with_live_progress(
                            context=context,
                            presentation_id=claim.presentation_id,
                            request=request,
                            task=task,
                            session=session,
                        )
                    await context.raise_if_cancelled()
                except WorkspaceOperationCancelled:
                    raise
                except WorkspaceOperationLeaseLost:
                    raise
                except (ValueError, HTTPException) as error:
                    raise NonRetryableWorkspaceOperationError("quality_review_unsupported_presentation") from error
                except Exception as error:
                    raise RetryableWorkspaceOperationError("quality_review_failed") from error

            if await context.progress(stage="quality_review_saving_results", percent=96) is False:
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
            current_revision = await session.scalar(
                select(PresentationModel.revision)
                .where(PresentationModel.id == claim.presentation_id)
                .execution_options(populate_existing=True)
            )
            if current_revision != inspected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "quality_review_revision_changed",
                    detail={
                        "inspected_revision": int(inspected_revision),
                        "current_revision": int(current_revision or 0),
                    },
                )
            await session.refresh(task)
            if task.status != "completed" or not isinstance(task.data, dict):
                raise RetryableWorkspaceOperationError("quality_review_failed")
            task_data = dict(task.data)
            persisted_release_base = task_data.pop(
                _RELEASE_REVIEW_BASE_TASK_KEY, None
            )
            if release_policy == "release-v1":
                assert isinstance(expected_runtime_binding_digest, str)
                assert isinstance(expected_source_render_identity_digest, str)
                assert isinstance(expected_checker_policy_digest, str)
                release_base_binding = _validate_release_review_base_binding(
                    persisted_release_base or release_base_binding,
                    operation_id=claim.operation_id,
                    source_revision=int(inspected_revision),
                    render_runtime_binding_digest=(
                        expected_runtime_binding_digest
                    ),
                    source_render_identity_digest=(
                        expected_source_render_identity_digest
                    ),
                    checker_policy_digest=expected_checker_policy_digest,
                )
                checker_policy = release_review_checker_policy()
                if not _release_review_task_policy_matches(task_data, checker_policy):
                    release_identity_changed = True
                    release_identity_change_reason = (
                        "release_quality_review_checker_changed"
                    )
            try:
                fixed_count = max(0, int(task_data.get("fixed_count") or 0))
            except (TypeError, ValueError) as error:
                raise RetryableWorkspaceOperationError("quality_review_failed") from error
            release_recovery_required = (
                _requires_release_recheck_after_completed_task(
                    release_policy=release_policy,
                    review_completed=review_completed,
                    fixed_count=fixed_count,
                )
            )
            if (
                release_policy == "release-v1"
                and release_identity_changed
                and not release_recovery_required
            ):
                raise NonRetryableWorkspaceOperationError(
                    release_identity_change_reason
                )
            fixed_slide_indices = task_data.get("fixed_slide_indices")
            if not isinstance(fixed_slide_indices, list):
                fixed_slide_indices = []
            normalized_fixed_slide_indices = [
                value
                for value in fixed_slide_indices
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            ]
            result_revision = int(inspected_revision) + 1 if fixed_count > 0 else int(inspected_revision)
            if fixed_count > 0:
                refreshed_slide_rows = await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == claim.presentation_id)
                    .order_by(SlideModel.index, SlideModel.id)
                    .execution_options(populate_existing=True)
                )
                try:
                    await refresh_authored_post_edit_evidence(
                        session,
                        presentation=presentation,
                        slides=list(refreshed_slide_rows.scalars()),
                        slide_indices=normalized_fixed_slide_indices,
                        source_revision=int(inspected_revision),
                        target_revision=result_revision,
                        operation_id=claim.operation_id,
                    )
                except AuthoredPostEditEvidenceError as error:
                    exception = (
                        RetryableWorkspaceOperationError
                        if error.retryable
                        else NonRetryableWorkspaceOperationError
                    )
                    raise exception(error.code) from error
            release_result_deck_html_digest: str | None = None
            release_result_render_identity_digest: str | None = None
            release_recheck_base: Mapping[str, Any] | None = None
            post_review_gate: Mapping[str, Any] | None = None
            if release_policy == "release-v1":
                if release_base_binding is None:
                    raise NonRetryableWorkspaceOperationError(
                        "release_quality_review_base_missing"
                    )
                result_slide_rows = await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == claim.presentation_id)
                    .order_by(SlideModel.index, SlideModel.id)
                    .execution_options(populate_existing=True)
                )
                result_slides = list(result_slide_rows.scalars())
                result_htmls = [slide.html_content for slide in result_slides]
                if not result_htmls or any(
                    not isinstance(html, str) or not html
                    for html in result_htmls
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "release_quality_review_authored_html_required"
                    )
                if not release_recovery_required:
                    try:
                        post_review_gate = await _evaluate_post_review_release_gate(
                            presentation=presentation,
                            slides=result_slides,
                            htmls=cast(list[str], result_htmls),
                            base_deck_html_digest=str(
                                release_base_binding["deckHtmlDigest"]
                            ),
                            render_runtime_binding_digest=(
                                expected_runtime_binding_digest
                            ),
                            source_render_identity_digest=(
                                expected_source_render_identity_digest
                            ),
                            checker_policy_digest=expected_checker_policy_digest,
                            source_readback_reissue_required=bool(
                                release_base_binding.get(
                                    "sourceReadbackReissueRequired"
                                )
                            ),
                            fixed_count=fixed_count,
                        )
                    except NonRetryableWorkspaceOperationError:
                        raise
                    except Exception as error:
                        raise RetryableWorkspaceOperationError(
                            "release_quality_review_post_gate_failed"
                        ) from error
                    release_result_deck_html_digest = str(
                        post_review_gate["resultDeckHtmlDigest"]
                    )
                    release_result_render_identity_digest = str(
                        post_review_gate["resultRenderIdentityDigest"]
                    )
                else:
                    release_result_deck_html_digest = digest_deck_htmls(
                        cast(list[str], result_htmls)
                    )
                    authored_gate = (
                        presentation.theme.get("authoredQualityGate")
                        if isinstance(presentation.theme, Mapping)
                        else None
                    )
                    source_readback_reissue_required = bool(
                        release_base_binding.get(
                            "sourceReadbackReissueRequired"
                        )
                        or not isinstance(authored_gate, Mapping)
                        or authored_gate.get("profileId") != "ordinary"
                    )
                    release_recheck_base = _build_release_recheck_base(
                        operation_id=claim.operation_id,
                        source_revision=int(inspected_revision),
                        result_revision=result_revision,
                        base_binding=release_base_binding,
                        result_deck_html_digest=release_result_deck_html_digest,
                        fixed_count=fixed_count,
                        source_readback_reissue_required=(
                            source_readback_reissue_required
                        ),
                    )
            result = {
                **task_data,
                "kind": QUALITY_REVIEW_OPERATION_KIND,
                "message": task.message,
                "mode": "analyze_and_fix",
                "scope": request.scope,
                "slide_indices": request.slide_indices,
                "inspected_revision": int(inspected_revision),
                "result_revision": result_revision,
                "fixed_count": fixed_count,
                "fixed_slide_indices": normalized_fixed_slide_indices,
                "canvas_refresh_required": fixed_count > 0,
                "canvas_refresh_slide_index": (
                    normalized_fixed_slide_indices[0] if normalized_fixed_slide_indices else None
                ),
                "canvas_refresh_status": ("requested" if fixed_count > 0 else "not_required"),
            }
            if release_policy == "release-v1":
                result["release_recheck_required"] = release_recovery_required
                if release_recheck_base is not None:
                    result["release_recheck_base"] = dict(release_recheck_base)
            if release_policy == "release-v1" and not release_recovery_required:
                if public_admission is None:
                    raise NonRetryableWorkspaceOperationError(
                        "release_quality_review_admission_required"
                    )
                if (
                    release_base_binding is None
                    or release_result_deck_html_digest is None
                    or release_result_render_identity_digest is None
                    or post_review_gate is None
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "release_quality_review_base_missing"
                    )
                await session.refresh(public_admission)
                public_checker_evidence: dict[str, Any] = {}
                result["release_review_receipt"] = build_export_release_review_receipt(
                    operation_id=claim.operation_id,
                    source_revision=int(inspected_revision),
                    result_revision=result_revision,
                    slide_count=int(
                        await session.scalar(
                            select(func.count())
                            .select_from(SlideModel)
                            .where(SlideModel.presentation == claim.presentation_id)
                        )
                        or 0
                    ),
                    review_result=task_data,
                    admission=public_admission,
                    base_deck_html_digest=str(
                        release_base_binding["deckHtmlDigest"]
                    ),
                    base_quality_receipt_digest=str(
                        release_base_binding["qualityReceiptDigest"]
                    ),
                    result_deck_html_digest=release_result_deck_html_digest,
                    render_runtime_binding_digest=(
                        expected_runtime_binding_digest
                    ),
                    source_render_identity_digest=(
                        expected_source_render_identity_digest
                    ),
                    result_render_identity_digest=(
                        release_result_render_identity_digest
                    ),
                    checker_policy_digest=expected_checker_policy_digest,
                    base_evidence_kind=str(
                        release_base_binding["baseEvidenceKind"]
                    ),
                    base_evidence_operation_id=(
                        str(release_base_binding["baseEvidenceOperationId"])
                        if release_base_binding.get("baseEvidenceOperationId")
                        is not None
                        else None
                    ),
                    post_review_gate=post_review_gate,
                    evidence_sink=public_checker_evidence,
                )
                await append_public_review_evidence(
                    session, operation_id=claim.operation_id,
                    attempt_token=claim.attempt_token, fencing_token=claim.fencing_token,
                    receipt=result["release_review_receipt"], evidence=public_checker_evidence,
                )
                if fixed_count == 0:
                    # No revision publication follows an unchanged review. Its
                    # checker event still needs to survive this session closing.
                    await session.commit()
            safe_result = sanitize_workspace_event_payload(
                result,
                operation_kind=QUALITY_REVIEW_OPERATION_KIND,
            )
            if fixed_count > 0:
                try:
                    published_revision = await publish_presentation_revision(
                        session,
                        presentation_id=claim.presentation_id,
                        expected_revision=int(inspected_revision),
                        operation_id=claim.operation_id,
                        attempt_token=claim.attempt_token,
                        result=safe_result,
                        operation_kind=QUALITY_REVIEW_OPERATION_KIND,
                    )
                    await session.commit()
                except PresentationPublicationConflict as error:
                    await session.rollback()
                    if str(error) in {
                        "stale_operation_fence",
                        "stale_operation_event_fence",
                    }:
                        raise WorkspaceOperationLeaseLost("operation_lease_lost") from error
                    raise NonRetryableWorkspaceOperationError("stale_presentation_revision") from error
                except Exception as error:
                    await session.rollback()
                    published = await self._published_result_after_commit_error(claim.operation_id)
                    if published is not None:
                        return published
                    raise RetryableWorkspaceOperationError("quality_review_result_commit_failed") from error
                if published_revision != result_revision:
                    raise RetryableWorkspaceOperationError("quality_review_result_commit_failed")
        return WorkspaceOperationResult(
            payload=safe_result,
            result_revision=result_revision,
        )
