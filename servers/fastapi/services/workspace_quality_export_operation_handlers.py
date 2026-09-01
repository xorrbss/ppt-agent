"""Quality review and immutable export workspace operations."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Literal, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.auth.internal import authenticated_internal_request_headers
from api.v2.contracts import QualityReceiptV3 as PublicQualityReceiptV3
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from services.authored_quality_review_service import (
    run_authored_quality_review as run_authored_quality_review,
)
from services.authored_evidence_ledger import digest_deck_htmls
from services.authored_vnext.generation.legacy_bridge import (
    export_persisted_legacy_master_deck,
)
from services.authored_vnext.generation.legacy_pin import (
    canonical_legacy_master_pin_identity,
)
from services.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from services.database import async_session_maker
from services.presentation_export_attempt_service import (
    begin_export_attempt,
    terminalize_export_attempt_failed,
)
from services.presentation_export_capabilities import (
    PresentationExportContractError,
)
from services.public_export_operation import (
    PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
    PublicExportOperationError,
    public_release_review_deck_render_identity_digest,
    public_release_review_runtime_binding,
    public_render_runtime_binding_digest,
    render_and_export_authored_htmls,
)
from services.authored_quality_receipt_v3_pipeline import (
    project_persisted_authored_quality_receipt_v3,
)
from services.public_quality_receipt_v3_store import load_public_quality_evidence
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_export_artifact_ref,
    prepare_workspace_generation_artifact,
    publish_workspace_export_artifact,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationDeferred,
    WorkspaceOperationExecutionError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)
from services.workspace_quality_operation_handler import (
    WorkspaceQualityReviewOperationHandler,
    release_review_checker_policy_digest,
    validate_release_review_base_evidence_for_export,
    validate_export_release_review_receipt,
)

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS = 30.0


def _validated_authored_quality_receipt_digest(
    theme: object,
    *,
    allow_visual_review_pending: bool = False,
) -> tuple[str, bool]:
    """Return a sealed V3 digest and whether only human visual review remains."""

    if not isinstance(theme, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    gate = theme.get("authoredQualityGate")
    receipt = gate.get("qualityReceiptV3") if isinstance(gate, Mapping) else None
    if not isinstance(receipt, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    blockers = receipt.get("blockers")
    visual_review_pending = (
        allow_visual_review_pending
        and receipt.get("gatePassed") is False
        and blockers == ["visualReview.required_failed"]
    )
    if receipt.get("schemaVersion") != "quality-receipt/v3" or not (
        receipt.get("gatePassed") is True or visual_review_pending
    ):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_blocked")
    receipt_digest = receipt.get("receiptDigest")
    sealed_fields = {
        "schemaVersion",
        "attemptId",
        "revision",
        "sequence",
        "eventType",
        "evidenceDigest",
        "capabilityRegistryDigest",
        "capabilityConsumerStatuses",
        "qualityTier",
        "t1Triggers",
        "blockers",
        "gatePassed",
        "previousSchemaVersion",
        "previousVersionReceiptDigest",
        "previousReceiptDigest",
    }
    if (
        not isinstance(receipt_digest, str)
        or not receipt_digest.startswith("sha256:")
        or len(receipt_digest) != 71
        or not sealed_fields.issubset(receipt)
    ):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_digest_invalid")
    sealed = {field: receipt[field] for field in sealed_fields}
    actual = "sha256:" + hashlib.sha256(
        json.dumps(
            sealed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if actual != receipt_digest:
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_digest_invalid")
    return receipt_digest.removeprefix("sha256:"), visual_review_pending


def _legacy_fidelity_quality_digest(scene_digest: str) -> str:
    """Bind legacy Fidelity reuse to the exact deck without claiming a quality pass."""

    payload = {
        "schemaVersion": "legacy-authored-fidelity-quality/v1",
        "sceneDigest": scene_digest,
        "releaseReadiness": "blocked",
        "reasonCode": "quality_receipt_v3_required",
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _resolved_authored_quality_digest(
    theme: object,
    *,
    scene_digest: str,
    pptx_mode: str,
) -> tuple[str, str | None]:
    """Resolve strict evidence, or the narrow legacy Fidelity compatibility binding."""

    try:
        digest, visual_review_pending = _validated_authored_quality_receipt_digest(
            theme,
            allow_visual_review_pending=pptx_mode == "fidelity",
        )
        return digest, "visual-review-pending" if visual_review_pending else None
    except NonRetryableWorkspaceOperationError as error:
        if error.code != "quality_receipt_v3_required" or pptx_mode != "fidelity":
            raise
        return _legacy_fidelity_quality_digest(scene_digest), "legacy"


def _legacy_fidelity_public_quality_receipt(
    *,
    quality_receipt_id: str,
    operation_id: str,
    presentation_id: str,
    artifact_digest: str,
    source_revision: int,
    scene_digest: str,
    evaluated_at: datetime,
) -> dict[str, object]:
    """Describe absent legacy checker evidence truthfully while allowing Fidelity delivery."""

    return PublicQualityReceiptV3(
        quality_receipt_id=quality_receipt_id,
        operation_id=operation_id,
        presentation_id=presentation_id,
        artifact_digest=artifact_digest,
        source_revision=source_revision,
        scene_digest=scene_digest,
        capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
        evaluated_at=evaluated_at,
        content_brief={"required": False, "status": "not_required"},
        authoring={
            "required": True,
            "status": "failed",
            "reasonCode": "legacy_authoring_evidence_missing",
        },
        geometry_font={
            "required": True,
            "status": "failed",
            "reasonCode": "legacy_geometry_font_evidence_missing",
        },
        image_content_integrity={"required": False, "status": "not_required"},
        visual_review={
            "required": False,
            "status": "unavailable",
            "reasonCode": "legacy_visual_review_evidence_missing",
        },
        performance={
            "required": False,
            "status": "not_assessed",
            "reasonCode": "legacy_performance_evidence_missing",
        },
        native_coverage_ooxml={
            "required": False,
            "status": "pending",
            "reasonCode": "ooxml_checker_evidence_not_persisted",
        },
        release_readiness="blocked",
    ).model_dump(mode="json", by_alias=True)


def _context(claim_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = claim_payload.get("context")
    return value if isinstance(value, Mapping) else {}


def _resolve_workspace_export_options(
    claim_payload: Mapping[str, Any],
    *,
    export_format: str,
) -> tuple[Literal["fidelity", "hybrid"], bool]:
    """Resolve existing workspace wire values without implicit Hybrid opt-in."""

    command_context = _context(claim_payload)
    raw_mode = command_context.get("pptx_mode")
    if raw_mode is None:
        pptx_mode: Literal["fidelity", "hybrid"] = "fidelity"
    elif raw_mode in {"fidelity", "hybrid"}:
        pptx_mode = cast(Literal["fidelity", "hybrid"], raw_mode)
    else:
        raise NonRetryableWorkspaceOperationError("workspace_export_pptx_mode_invalid")

    font_embedding = command_context.get("font_embedding", False)
    if not isinstance(font_embedding, bool):
        raise NonRetryableWorkspaceOperationError("workspace_export_font_embedding_invalid")
    if font_embedding and (export_format != "pptx" or pptx_mode != "hybrid"):
        raise NonRetryableWorkspaceOperationError("workspace_export_font_embedding_requires_hybrid")
    return pptx_mode, font_embedding


def _validate_authored_vnext_export_request(
    claim_payload: Mapping[str, Any],
    *,
    projection: object,
    export_format: str,
    pptx_mode: Literal["fidelity", "hybrid"],
    font_embedding: bool,
) -> None:
    """Fence native export to the immutable document pin and its native mode."""

    if not isinstance(projection, Mapping):
        raise NonRetryableWorkspaceOperationError(
            "authored_vnext_export_projection_incomplete"
        )
    projected_pin = canonical_legacy_master_pin_identity(projection.get("masterPin"))
    requested_pin_payload = _context(claim_payload).get("master_pin")
    requested_pin = (
        canonical_legacy_master_pin_identity(requested_pin_payload)
        if requested_pin_payload is not None
        else projected_pin
    )
    if projected_pin is None or requested_pin != projected_pin:
        raise NonRetryableWorkspaceOperationError(
            "authored_vnext_export_master_changed"
        )
    if export_format == "pptx" and (pptx_mode != "fidelity" or font_embedding):
        raise NonRetryableWorkspaceOperationError(
            "authored_vnext_native_export_mode_required"
        )


def _authored_vnext_export_path(
    *,
    projection_present: bool,
    export_format: str,
    pptx_mode: Literal["fidelity", "hybrid"],
) -> Literal["shared", "legacy-native", "semantic-hybrid"]:
    if not projection_present:
        return "shared"
    # PDF is a browser/raster publication and owns no legacy native master
    # reconstruction. Route it through the same semantic-safe shared boundary
    # as Hybrid so an absent company-standard pin cannot manufacture a
    # master_changed failure.
    if export_format == "pdf":
        return "semantic-hybrid"
    if pptx_mode == "hybrid":
        return "semantic-hybrid"
    return "legacy-native"


class WorkspaceExportOperationHandler:
    """Render then immutably publish an owner-scoped export artifact."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        internal_header_provider: InternalHeaderProvider = authenticated_internal_request_headers,
    ) -> None:
        self._session_factory = session_factory
        self._internal_header_provider = internal_header_provider

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        export_format = claim.kind.removeprefix("export.")
        if export_format not in {"pptx", "pdf"}:
            raise NonRetryableWorkspaceOperationError("workspace_export_format_invalid")
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")
        pptx_mode, font_embedding = _resolve_workspace_export_options(
            claim.request_payload,
            export_format=export_format,
        )

        await context.raise_if_cancelled()
        public_tenant_id: str | None = None
        release_review_receipt_digest: str | None = None
        release_review_base_receipt_digest: str | None = None
        release_review_result_deck_html_digest: str | None = None
        release_review_runtime_binding_digest: str | None = None
        release_review_result_render_identity_digest: str | None = None
        sealed_release_review_checker_policy_digest: str | None = None
        review_evidence_operation_id: uuid.UUID | None = None
        effective_revision = claim.expected_revision
        effective_claim = claim
        async with self._session_factory() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            title = presentation.title or "presentation"
            authored_vnext_projection = (
                presentation.theme.get("authored_vnext")
                if isinstance(presentation.theme, Mapping)
                else None
            )
            authored_vnext_export_path = _authored_vnext_export_path(
                projection_present=authored_vnext_projection is not None,
                export_format=export_format,
                pptx_mode=pptx_mode,
            )
            admission = await session.scalar(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == claim.operation_id
                )
            )
            if admission is not None and admission.state == "active":
                public_tenant_id = admission.tenant_id
                # Public requests never execute Chrome or an OOXML converter in
                # the durable operation/Core process. The isolated raster path
                # is the public boundary; richer desktop/private modes retain
                # their existing adapters outside this admission.
                authored_vnext_export_path = "shared"
                if claim.request_payload.get("quality_policy") == "release-v1":
                    raw_reviewed_revision = claim.request_payload.get(
                        "reviewed_source_revision", claim.expected_revision
                    )
                    if (
                        isinstance(raw_reviewed_revision, bool)
                        or not isinstance(raw_reviewed_revision, int)
                        or raw_reviewed_revision < 0
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_required"
                        )
                    reviewed_revision = raw_reviewed_revision
                    raw_review_operation_id = claim.request_payload.get(
                        "release_review_operation_id"
                    )
                    try:
                        review_operation_id = uuid.UUID(str(raw_review_operation_id))
                    except (TypeError, ValueError) as error:
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_required"
                        ) from error
                    review_operation = await session.get(
                        WorkspaceOperation, review_operation_id
                    )
                    if (
                        review_operation is None
                        or review_operation.presentation_id != claim.presentation_id
                        or review_operation.owner_id != claim.owner_id
                        or review_operation.owner_scope != claim.owner_scope
                        or review_operation.kind != QUALITY_REVIEW_OPERATION_KIND
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_required"
                        )
                    review_admission = await session.scalar(
                        select(PublicOperationAdmission).where(
                            PublicOperationAdmission.operation_id
                            == review_operation.id,
                            PublicOperationAdmission.owner_id == claim.owner_id,
                            PublicOperationAdmission.tenant_id == public_tenant_id,
                            PublicOperationAdmission.operation_kind == "generation",
                        )
                    )
                    if review_admission is None:
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_authority_invalid"
                        )
                    if (
                        review_operation.state
                        in {
                            "queued",
                            "waiting_for_assets",
                            "blocked",
                            "running",
                            "approval_required",
                            "awaiting_approval",
                            "cancelling",
                        }
                        and review_admission.state in {"queued", "active"}
                    ) or (
                        review_operation.state == "succeeded"
                        and review_admission.state in {"queued", "active"}
                    ):
                        raise WorkspaceOperationDeferred(
                            "release_quality_review_pending",
                            retry_after_seconds=1.0,
                            detail={
                                "release_review_operation_id": str(review_operation.id)
                            },
                        )
                    if (
                        review_operation.state != "succeeded"
                        or review_admission.state != "succeeded"
                        or review_operation.expected_revision != reviewed_revision
                        or review_operation.result_revision is None
                        or not isinstance(review_operation.result, Mapping)
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_failed"
                        )
                    effective_revision = int(review_operation.result_revision)
                    release_review_receipt = review_operation.result.get(
                        "release_review_receipt"
                    )
                    release_review_receipt_digest = (
                        validate_export_release_review_receipt(
                            release_review_receipt,
                            operation_id=review_operation.id,
                            source_revision=reviewed_revision,
                            result_revision=effective_revision,
                        )
                    )
                    review_evidence_operation_id = review_operation.id
                    if not isinstance(release_review_receipt, Mapping):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_required"
                        )
                    release_review_result_deck_html_digest = str(
                        release_review_receipt.get("resultDeckHtmlDigest", "")
                    )
                    release_review_runtime_binding_digest = str(
                        release_review_receipt.get(
                            "renderRuntimeBindingDigest", ""
                        )
                    )
                    release_review_result_render_identity_digest = str(
                        release_review_receipt.get(
                            "resultRenderIdentityDigest", ""
                        )
                    )
                    sealed_release_review_checker_policy_digest = str(
                        release_review_receipt.get("checkerPolicyDigest", "")
                    )
                    try:
                        current_review_runtime_binding_digest = (
                            public_render_runtime_binding_digest(
                                public_release_review_runtime_binding()
                            )
                        )
                    except PublicExportOperationError as error:
                        raise NonRetryableWorkspaceOperationError(
                            str(error)
                        ) from error
                    if (
                        release_review_runtime_binding_digest
                        != current_review_runtime_binding_digest
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_runtime_changed"
                        )
                    if (
                        sealed_release_review_checker_policy_digest
                        != release_review_checker_policy_digest()
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_checker_changed"
                        )
                    release_review_base_receipt_digest, _ = (
                        await validate_release_review_base_evidence_for_export(
                            session,
                            presentation=presentation,
                            review_operation=review_operation,
                            receipt=release_review_receipt,
                        )
                    )
                    if presentation.revision != effective_revision:
                        raise NonRetryableWorkspaceOperationError(
                            "stale_presentation_revision"
                        )
            if (
                authored_vnext_projection is not None
                and public_tenant_id is None
                and authored_vnext_export_path != "semantic-hybrid"
            ):
                _validate_authored_vnext_export_request(
                    claim.request_payload,
                    projection=authored_vnext_projection,
                    export_format=export_format,
                    pptx_mode=pptx_mode,
                    font_embedding=font_embedding,
                )
            operation = await session.get(WorkspaceOperation, claim.operation_id)
            if (
                operation is None
                or operation.presentation_id != claim.presentation_id
                or operation.owner_id != claim.owner_id
                or operation.owner_scope != claim.owner_scope
            ):
                raise NonRetryableWorkspaceOperationError("workspace_export_operation_invalid")
            if operation.cancel_requested_at is not None:
                raise WorkspaceOperationCancelled("workspace_operation_cancelled")
            if (
                operation.state != "running"
                or operation.attempt_token != claim.attempt_token
                or operation.fencing_token != claim.fencing_token
            ):
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
            if operation.expected_revision != effective_revision:
                locked_revision = await session.scalar(
                    select(PresentationModel.revision)
                    .where(PresentationModel.id == claim.presentation_id)
                    .with_for_update()
                )
                if locked_revision != effective_revision:
                    await session.rollback()
                    raise NonRetryableWorkspaceOperationError(
                        "stale_presentation_revision"
                    )
                request_payload = dict(operation.request_payload)
                request_payload["source_revision"] = effective_revision
                request_payload["reviewed_source_revision"] = claim.expected_revision
                updated = await session.execute(
                    update(WorkspaceOperation)
                    .where(
                        WorkspaceOperation.id == claim.operation_id,
                        WorkspaceOperation.presentation_id
                        == claim.presentation_id,
                        WorkspaceOperation.owner_scope == claim.owner_scope,
                        WorkspaceOperation.state == "running",
                        WorkspaceOperation.attempt_token == claim.attempt_token,
                        WorkspaceOperation.fencing_token == claim.fencing_token,
                        WorkspaceOperation.cancel_requested_at.is_(None),
                        WorkspaceOperation.expected_revision
                        == claim.expected_revision,
                        WorkspaceOperation.version == operation.version,
                    )
                    .values(
                        expected_revision=effective_revision,
                        request_payload=request_payload,
                        version=WorkspaceOperation.version + 1,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                if updated.rowcount != 1:
                    await session.rollback()
                    raise WorkspaceOperationLeaseLost("operation_lease_lost")
                await session.commit()
                effective_claim = replace(
                    claim,
                    expected_revision=effective_revision,
                    request_payload=request_payload,
                )
            elif presentation.revision != effective_revision:
                raise NonRetryableWorkspaceOperationError("stale_presentation_revision")

        await context.progress(stage="export_rendering", percent=10)
        execution = None
        native_export = None
        native_export_attempt_id = None
        direct_export_selected_adapter: str | None = None
        public_artifact_id = None
        public_ref = None
        worker_resource_metrics: Mapping[str, Any] | None = None
        quality_receipt_digest: str | None = None
        authored_quality_gate: object | None = None
        authored_scene_digest: str | None = None
        public_quality_receipt: dict[str, object] | None = None
        fidelity_quality_compatibility: str | None = None
        try:
            if authored_vnext_export_path == "legacy-native":
                if not isinstance(authored_vnext_projection, dict):
                    raise PresentationExportContractError(
                        "authored_vnext_export_projection_incomplete"
                    )
                deck_digest = authored_vnext_projection.get("deckDigest")
                master_pin = canonical_legacy_master_pin_identity(
                    authored_vnext_projection.get("masterPin")
                )
                if (
                    not isinstance(deck_digest, str)
                    or not deck_digest.startswith("sha256:")
                    or len(deck_digest) != 71
                    or any(
                        character not in "0123456789abcdef"
                        for character in deck_digest.removeprefix("sha256:")
                    )
                    or master_pin is None
                ):
                    raise PresentationExportContractError(
                        "authored_vnext_export_projection_incomplete"
                    )
                source_snapshot_sha256 = deck_digest.removeprefix("sha256:")
                initial_provenance = {
                    "schema_version": 1,
                    "adapter_chain": ["native_reconstructed"],
                    "capability_evidence": {
                        "authored_vnext_native_master": True,
                        "deck_digest": deck_digest,
                        "master_digest": master_pin[2],
                    },
                    "fallback_reason": None,
                    "asset_digests": {},
                    "compiler_runtime": {
                        "compiler": "OoxmlMasterCompilerPort",
                        "compiler_version": "ooxml-master-port/v1",
                        "runtime": "authored-vnext-ooxml-worker",
                        "runtime_version": "1",
                    },
                    "freshness": {
                        "source_revision": effective_revision,
                        "source_snapshot_sha256": source_snapshot_sha256,
                        "package_sha256": None,
                        "asset_digests_match": True,
                        "package_digest_match": False,
                    },
                }
                async with self._session_factory() as session:
                    attempt = await begin_export_attempt(
                        session,
                        presentation_id=claim.presentation_id,
                        owner_id=claim.owner_id,
                        owner_scope=claim.owner_scope,
                        workspace_operation_id=claim.operation_id,
                        export_format="pptx",
                        source_revision=effective_revision,
                        source_snapshot_sha256=source_snapshot_sha256,
                        request_hash=operation.request_hash,
                        idempotency_key_hash=operation.idempotency_key_hash,
                        requested_adapter="native_reconstructed",
                        provenance=initial_provenance,
                    )
                export_attempt_id = attempt.id
                native_export_attempt_id = attempt.id
                direct_export_selected_adapter = "native_reconstructed"
                try:
                    native_export = await export_persisted_legacy_master_deck(
                        owner_id=claim.owner_id,
                        projection=authored_vnext_projection,
                    )
                except Exception as error:
                    raise PresentationExportContractError(
                        getattr(
                            error,
                            "code",
                            str(error)
                            if str(error).startswith("authored_vnext_")
                            else "authored_vnext_native_export_failed",
                        )
                    ) from error
                export_path = native_export.path
                artifact_sha256 = native_export.artifact_sha256
                artifact_size_bytes = native_export.artifact_size_bytes
                source_snapshot_sha256 = native_export.source_snapshot_sha256
                selected_adapter = "native_reconstructed"
                export_provenance = {
                    **native_export.provenance,
                    "freshness": {
                        **native_export.provenance["freshness"],
                        "source_revision": effective_revision,
                    },
                }
            else:
                if pptx_mode != "fidelity" or font_embedding:
                    raise NonRetryableWorkspaceOperationError(
                        "public_isolated_hybrid_not_supported"
                    )
                async with self._session_factory() as session:
                    slides = list(
                        (
                            await session.execute(
                                select(SlideModel)
                                .where(SlideModel.presentation == claim.presentation_id)
                                .order_by(SlideModel.index, SlideModel.id)
                            )
                        ).scalars()
                    )
                if public_tenant_id is None:
                    raise NonRetryableWorkspaceOperationError(
                        "public_export_admission_required"
                    )
                async with self._session_factory() as session:
                    admission = await session.scalar(
                        select(PublicOperationAdmission).where(
                            PublicOperationAdmission.operation_id == claim.operation_id
                        )
                    )
                    if admission is None or admission.state != "active":
                        raise NonRetryableWorkspaceOperationError(
                            "public_export_admission_required"
                        )
                    public_tenant_id = admission.tenant_id
                    authored_quality_gate = presentation.theme.get(
                        "authoredQualityGate"
                    )
                    if release_review_receipt_digest is not None:
                        if release_review_base_receipt_digest is None:
                            raise NonRetryableWorkspaceOperationError(
                                "quality_receipt_v3_required"
                            )
                        quality_receipt_digest = hashlib.sha256(
                            (
                                release_review_base_receipt_digest
                                + ":"
                                + release_review_receipt_digest
                            ).encode("ascii")
                        ).hexdigest()
                htmls = [slide.html_content for slide in slides]
                if not htmls or any(not isinstance(html, str) or not html for html in htmls):
                    raise NonRetryableWorkspaceOperationError(
                        "public_isolated_authored_html_required"
                    )
                authored_scene_digest = digest_deck_htmls(cast(list[str], htmls))
                if release_review_receipt_digest is None:
                    quality_receipt_digest, fidelity_quality_compatibility = (
                        _resolved_authored_quality_digest(
                            presentation.theme,
                            scene_digest=authored_scene_digest,
                            pptx_mode=pptx_mode,
                        )
                    )
                if (
                    release_review_result_deck_html_digest is not None
                    and authored_scene_digest
                    != release_review_result_deck_html_digest
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "release_quality_review_result_drift"
                    )
                if release_review_result_render_identity_digest is not None:
                    try:
                        current_result_render_identity_digest = (
                            public_release_review_deck_render_identity_digest(
                                cast(list[str], htmls)
                            )
                        )
                    except PublicExportOperationError as error:
                        raise NonRetryableWorkspaceOperationError(
                            str(error)
                        ) from error
                    if (
                        current_result_render_identity_digest
                        != release_review_result_render_identity_digest
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "release_quality_review_render_identity_changed"
                        )
                source_snapshot_sha256 = hashlib.sha256(
                    json.dumps(
                        htmls,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                initial_provenance = {
                    "schema_version": 1,
                    "adapter_chain": ["general"],
                    "capability_evidence": {
                        "selected_adapter": "general",
                        "execution_profile": "isolated_raster_v1",
                        # Remains false until the completed worker samples carry
                        # production-eligible OS isolation attestations.
                        "network_none_worker": False,
                        "isolation_attestations": [],
                        "sessionless_capability": True,
                        "pptx_mode": "fidelity" if export_format == "pptx" else None,
                        "legacy_quality_compatibility": (
                            fidelity_quality_compatibility == "legacy"
                        ),
                        "visual_review_pending": (
                            fidelity_quality_compatibility == "visual-review-pending"
                        ),
                        "release_readiness": (
                            "blocked"
                            if fidelity_quality_compatibility is not None
                            else "evidence_bound"
                        ),
                    },
                    "fallback_reason": None,
                    "asset_digests": {},
                    "compiler_runtime": {
                        "compiler": "public-isolated-export",
                        "compiler_version": "1",
                        "runtime": "render-export-worker",
                        "runtime_version": PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
                    },
                    "freshness": {
                        "source_revision": effective_revision,
                        "source_snapshot_sha256": source_snapshot_sha256,
                        "package_sha256": None,
                        "asset_digests_match": True,
                        "package_digest_match": False,
                    },
                    "render_scene": {
                        "schema_version": "authored-html-render-scene/v1",
                        "scene_digest": authored_scene_digest,
                        "ordered_slide_count": len(htmls),
                    },
                }
                async with self._session_factory() as session:
                    attempt = await begin_export_attempt(
                        session,
                        presentation_id=claim.presentation_id,
                        owner_id=claim.owner_id,
                        owner_scope=claim.owner_scope,
                        workspace_operation_id=claim.operation_id,
                        export_format=cast(Literal["pptx", "pdf"], export_format),
                        source_revision=effective_revision,
                        source_snapshot_sha256=source_snapshot_sha256,
                        request_hash=operation.request_hash,
                        idempotency_key_hash=operation.idempotency_key_hash,
                        requested_adapter="general",
                        provenance=initial_provenance,
                    )
                export_attempt_id = attempt.id
                native_export_attempt_id = attempt.id
                direct_export_selected_adapter = "general"
                public_execution = await render_and_export_authored_htmls(
                    effective_claim,
                    tenant_id=public_tenant_id,
                    htmls=cast(list[str], htmls),
                    export_format=export_format,
                    scene_digest=(authored_scene_digest or ""),
                    quality_digest=quality_receipt_digest,
                    expected_render_runtime_binding_digest=(
                        release_review_runtime_binding_digest
                    ),
                    expected_deck_render_identity_digest=(
                        release_review_result_render_identity_digest
                    ),
                    session_factory=self._session_factory,
                )
                public_ref = public_execution.artifact
                worker_resource_metrics = public_execution.resource_metrics
                public_artifact_id = public_ref.artifact_id
                artifact_sha256 = public_ref.sha256
                artifact_size_bytes = public_ref.size_bytes
                selected_adapter = "general"
                export_provenance = {
                    **initial_provenance,
                    "capability_evidence": {
                        **initial_provenance["capability_evidence"],
                        "network_none_worker": (
                            worker_resource_metrics.get("network_none_worker") is True
                        ),
                        "isolation_attestations": list(
                            worker_resource_metrics.get(
                                "worker_isolation_attestations", []
                            )
                        ),
                    },
                    "freshness": {
                        **initial_provenance["freshness"],
                        "package_sha256": artifact_sha256,
                        "package_digest_match": True,
                    },
                }
                if (
                    authored_scene_digest is None
                    or not authored_scene_digest.startswith("sha256:")
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "public_quality_projection_inputs_missing"
                    )
                if fidelity_quality_compatibility == "legacy":
                    public_quality_receipt = _legacy_fidelity_public_quality_receipt(
                        quality_receipt_id=(
                            f"quality:{claim.operation_id}:{public_ref.artifact_id}"
                        ),
                        operation_id=str(claim.operation_id),
                        presentation_id=str(claim.presentation_id),
                        artifact_digest=f"sha256:{public_ref.sha256}",
                        source_revision=effective_revision,
                        scene_digest=authored_scene_digest,
                        evaluated_at=datetime.now(timezone.utc),
                    )
                else:
                    if authored_quality_gate is None:
                        raise NonRetryableWorkspaceOperationError(
                            "public_quality_projection_inputs_missing"
                        )
                    generation_receipt = (
                        authored_quality_gate.get("qualityReceiptV3")
                        if isinstance(authored_quality_gate, Mapping) else None
                    )
                    async with self._session_factory() as evidence_session:
                        generation_evidence_record = await load_public_quality_evidence(
                            evidence_session,
                            owner_id=claim.owner_id,
                            owner_scope=claim.owner_scope,
                            presentation_id=claim.presentation_id,
                            receipt_digest=(
                                str(generation_receipt.get("receiptDigest", ""))
                                if isinstance(generation_receipt, Mapping) else ""
                            ),
                        )
                        review_evidence_record = (
                            await load_public_quality_evidence(
                                evidence_session,
                                owner_id=claim.owner_id,
                                owner_scope=claim.owner_scope,
                                presentation_id=claim.presentation_id,
                                receipt_digest=f"sha256:{release_review_receipt_digest}",
                                operation_id=review_evidence_operation_id,
                            ) if review_evidence_operation_id is not None else None
                        )
                    public_quality_receipt = (
                        project_persisted_authored_quality_receipt_v3(
                            authored_quality_gate=authored_quality_gate,
                            quality_receipt_id=(
                                f"quality:{claim.operation_id}:{public_ref.artifact_id}"
                            ),
                            operation_id=str(claim.operation_id),
                            presentation_id=str(claim.presentation_id),
                            artifact_digest=f"sha256:{public_ref.sha256}",
                            source_revision=effective_revision,
                            scene_digest=authored_scene_digest,
                            exporter_version=PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
                            evaluated_at=datetime.now(timezone.utc),
                            generation_evidence_record=generation_evidence_record,
                            review_evidence_record=review_evidence_record,
                            render_runtime_binding_digest=(
                                release_review_runtime_binding_digest
                            ),
                            result_render_identity_digest=(
                                release_review_result_render_identity_digest
                            ),
                            checker_policy_digest=(
                                sealed_release_review_checker_policy_digest
                            ),
                        ).model_dump(mode="json", by_alias=True)
                    )
            if public_ref is None and artifact_sha256 != hashlib.sha256(
                export_path.read_bytes()
            ).hexdigest():
                raise PresentationExportContractError(
                    "presentation_export_artifact_digest_mismatch"
                )
            await context.raise_if_cancelled()
            await context.progress(stage="export_publishing", percent=80)
            async with self._session_factory() as session:
                if public_artifact_id is not None:
                    if public_ref is None:
                        raise WorkspaceArtifactPublicationError(
                            "workspace_public_artifact_binding_invalid"
                        )
                    artifact = await prepare_workspace_export_artifact_ref(
                        session,
                        reference=public_ref,
                        operation_id=claim.operation_id,
                        presentation_id=claim.presentation_id,
                        source_revision=effective_revision,
                        kind=claim.kind,
                        export_attempt_id=export_attempt_id,
                    )
                else:
                    if artifact_sha256 != hashlib.sha256(
                        export_path.read_bytes()
                    ).hexdigest():
                        raise PresentationExportContractError(
                            "presentation_export_artifact_digest_mismatch"
                        )
                    artifact = await prepare_workspace_generation_artifact(
                        session,
                        source_path=str(export_path),
                        operation_id=claim.operation_id,
                        presentation_id=claim.presentation_id,
                        source_revision=effective_revision,
                        kind=claim.kind,
                        export_attempt_id=export_attempt_id,
                        expected_source_sha256=artifact_sha256,
                        expected_source_size_bytes=artifact_size_bytes,
                    )
                try:
                    await context.raise_if_cancelled()
                except WorkspaceOperationCancelled:
                    await compensate_workspace_artifact(session, artifact)
                    raise
                revision = await publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token=claim.attempt_token,
                    fencing_token=claim.fencing_token,
                    export_format=export_format,
                    export_source_snapshot_sha256=source_snapshot_sha256,
                    export_selected_adapter=selected_adapter,
                    export_provenance=export_provenance,
                    worker_resource_metrics=worker_resource_metrics,
                    public_artifact_id=public_artifact_id,
                    public_tenant_id=(
                        public_tenant_id if public_artifact_id is not None else None
                    ),
                    public_attempt_number=(
                        claim.attempt_number if public_artifact_id is not None else None
                    ),
                    public_quality_digest=quality_receipt_digest,
                    public_quality_receipt=public_quality_receipt,
                )
        except WorkspaceOperationCancelled:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(execution.workspace_operation_id),
                        failure_code="presentation_export_cancelled",
                        failure_path=None,
                    )
            elif native_export_attempt_id is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=native_export_attempt_id,
                        expected_owner_id=claim.owner_id,
                        expected_owner_scope=claim.owner_scope,
                        expected_workspace_operation_id=claim.operation_id,
                        failure_code="presentation_export_cancelled",
                        failure_path=None,
                        selected_adapter=(direct_export_selected_adapter or "native_reconstructed"),
                    )
            raise
        except WorkspaceArtifactPublicationError as error:
            error_type = (
                NonRetryableWorkspaceOperationError
                if error.code
                in {
                    "stale_presentation_revision",
                    "operation_lease_lost",
                    "workspace_export_format_invalid",
                }
                else RetryableWorkspaceOperationError
            )
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(execution.workspace_operation_id),
                        failure_code=error.code,
                        failure_path=None,
                    )
            elif native_export_attempt_id is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=native_export_attempt_id,
                        expected_owner_id=claim.owner_id,
                        expected_owner_scope=claim.owner_scope,
                        expected_workspace_operation_id=claim.operation_id,
                        failure_code=error.code,
                        failure_path=None,
                        selected_adapter=(direct_export_selected_adapter or "native_reconstructed"),
                    )
            if error.code == "operation_lease_lost":
                raise WorkspaceOperationLeaseLost(error.code) from error
            raise error_type(error.code) from error
        except WorkspaceOperationLeaseLost:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(execution.workspace_operation_id),
                        failure_code="operation_lease_lost",
                        failure_path=None,
                    )
            elif native_export_attempt_id is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=native_export_attempt_id,
                        expected_owner_id=claim.owner_id,
                        expected_owner_scope=claim.owner_scope,
                        expected_workspace_operation_id=claim.operation_id,
                        failure_code="operation_lease_lost",
                        failure_path=None,
                        selected_adapter=(direct_export_selected_adapter or "native_reconstructed"),
                    )
            raise
        except PresentationExportContractError as error:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(execution.workspace_operation_id),
                        failure_code=error.code,
                        failure_path=error.path,
                    )
            elif native_export_attempt_id is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=native_export_attempt_id,
                        expected_owner_id=claim.owner_id,
                        expected_owner_scope=claim.owner_scope,
                        expected_workspace_operation_id=claim.operation_id,
                        failure_code=error.code,
                        failure_path=error.path,
                        selected_adapter=(direct_export_selected_adapter or "native_reconstructed"),
                    )
            raise NonRetryableWorkspaceOperationError(error.code) from error
        except WorkspaceOperationExecutionError as error:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(execution.workspace_operation_id),
                        failure_code=error.code,
                        failure_path=None,
                    )
            elif native_export_attempt_id is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=native_export_attempt_id,
                        expected_owner_id=claim.owner_id,
                        expected_owner_scope=claim.owner_scope,
                        expected_workspace_operation_id=claim.operation_id,
                        failure_code=error.code,
                        failure_path=None,
                        selected_adapter=(
                            direct_export_selected_adapter or "native_reconstructed"
                        ),
                    )
            raise
        except Exception as error:
            LOGGER.exception(
                "Workspace presentation export failed after execution",
                extra={
                    "operation_id": str(claim.operation_id),
                    "presentation_id": str(claim.presentation_id),
                    "format": export_format,
                    "export_attempt_id": (str(execution.attempt_id) if execution is not None else None),
                },
            )
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(execution.workspace_operation_id),
                        failure_code=getattr(
                            error,
                            "code",
                            "presentation_export_unexpected_failure",
                        ),
                        failure_path=getattr(error, "path", None),
                    )
            elif native_export_attempt_id is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=native_export_attempt_id,
                        expected_owner_id=claim.owner_id,
                        expected_owner_scope=claim.owner_scope,
                        expected_workspace_operation_id=claim.operation_id,
                        failure_code=getattr(
                            error,
                            "code",
                            "presentation_export_unexpected_failure",
                        ),
                        failure_path=getattr(error, "path", None),
                        selected_adapter=(direct_export_selected_adapter or "native_reconstructed"),
                    )
            resource_detail = getattr(error, "resource_metrics", None)
            raise RetryableWorkspaceOperationError(
                "presentation_export_failed",
                detail=(dict(resource_detail) if isinstance(resource_detail, Mapping) else None),
            ) from error
        await context.progress(stage="export_completed", percent=100)
        # Publication has already terminalized the operation. The runner will
        # reconcile this canonical durable projection rather than append twice.
        return WorkspaceOperationResult(
            payload={
                "status": "completed",
                "format": export_format,
                **dict(worker_resource_metrics or {}),
            },
            result_revision=revision,
        )


def register_workspace_quality_export_operation_handlers(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> tuple[WorkspaceQualityReviewOperationHandler, WorkspaceExportOperationHandler]:
    quality = WorkspaceQualityReviewOperationHandler()
    export = WorkspaceExportOperationHandler()
    registry.register(
        QUALITY_REVIEW_OPERATION_KIND,
        quality,
        replace=replace,
        recovery_policy="automatic",
    )
    for kind in EXPORT_OPERATION_KINDS:
        registry.register(kind, export, replace=replace)
    return quality, export
