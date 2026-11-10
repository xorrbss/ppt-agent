"""Quality review and immutable export workspace operations."""

from __future__ import annotations

from services.workspace_quality_receipt_digest import (
    _is_sha256_digest as _is_sha256_digest,
    _release_review_receipt_digest as _release_review_receipt_digest,
)

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
from models.sql.public_admission import PublicOperationAdmission
from services.authored_quality_receipt_v2 import (
    validate_current_quality_receipt_payload,
    validate_quality_receipt_v3_payload,
)
from services.authored_quality_telemetry import style_comparison_observation_metadata
from services.database import async_session_maker
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationResult,
)
from utils.slide_capture import (
    CACHE_IDENTITY_VERSION,
    DOM_METRICS_VERSION,
    MEASUREMENT_CACHE_VERSION,
    OCCLUSION_SENSOR_VERSION,
)

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
# The review writes its slide fixes on the session it holds open until the final
# revision bump, so its lease must cover the whole pass rather than one renewal.
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
_RELEASE_REVIEW_RECEIPT_SCHEMA = "export-release-review-receipt/v2"
_RELEASE_REVIEW_CHECKER_POLICY_SCHEMA = "public-release-review-checker-policy/v1"
_RELEASE_REVIEW_IMPLEMENTATION_VERSION = "release-review-h0-h1/v2"
_RELEASE_REVIEW_CAPACITY_POLICY_VERSION = "authored-capacity-policy/v1"
_RELEASE_REVIEW_QID_POLICY_VERSION = "declared-authored-qids/v1"
_RELEASE_RECHECK_BASE_SCHEMA = "export-release-recheck-base/v1"
_BASE_EVIDENCE_GENERATION = "generation-quality-v3"
_BASE_EVIDENCE_REVIEW = "release-review-transition"
_BASE_EVIDENCE_RECOVERY = "release-recheck-transition"


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
    """Run the svg quality review (``mode="analyze_only"``).

    ``analyze_and_fix`` was the authored review, which repaired pages through
    SlideSpec; it was removed with the authored strategy (2026-11-10) and is now
    refused like any other unknown mode.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")
        mode = _context(claim.request_payload).get("mode", "analyze_and_fix")
        if mode != "analyze_only":
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        from services.svg_quality_review_service import execute_svg_quality_review

        return await execute_svg_quality_review(context, self._session_factory)
