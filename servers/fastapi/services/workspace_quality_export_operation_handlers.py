"""Quality review and immutable export workspace operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.auth.internal import authenticated_internal_request_headers
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicOperationAdmission
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceArtifact, WorkspaceOperation
from services.artifact_store import configured_public_artifact_service
from services.authored_quality_review_service import (
    run_authored_quality_review as run_authored_quality_review,
)
from services.authored_vnext.generation.legacy_bridge import (
    export_persisted_legacy_master_deck,
)
from services.authored_vnext.generation.legacy_pin import (
    canonical_legacy_master_pin_identity,
)
from services.database import async_session_maker
from services.presentation_export_attempt_service import (
    begin_export_attempt,
    terminalize_export_attempt_failed,
)
from services.presentation_export_capabilities import (
    PresentationExportContractError,
)
from services.public_export_operation import (
    PublicExportOperationError,
    render_and_export_authored_htmls,
)
from services.authored_quality_receipt_v3_pipeline import (
    project_persisted_authored_quality_receipt_v3,
)
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_generation_artifact,
    publish_workspace_export_artifact,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)
from services.workspace_quality_operation_handler import (
    WorkspaceQualityReviewOperationHandler,
)
from utils.get_env import get_app_data_directory_env

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS = 30.0
ARTIFACT_EXPORT_DURABILITY_SHADOW_ENV = "PRESENTON_ARTIFACT_EXPORT_DURABILITY_SHADOW"


def _validated_authored_quality_receipt_digest(theme: object) -> str:
    """Return the sealed V3 digest; absent or synthetic PASS evidence blocks export."""

    if not isinstance(theme, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    gate = theme.get("authoredQualityGate")
    receipt = gate.get("qualityReceiptV3") if isinstance(gate, Mapping) else None
    if not isinstance(receipt, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    if (
        receipt.get("schemaVersion") != "quality-receipt/v3"
        or receipt.get("gatePassed") is not True
        or receipt.get("qualityStatus") != "passed"
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
    return receipt_digest.removeprefix("sha256:")


def _artifact_export_durability_shadow_enabled() -> bool:
    """Keep the durable-artifact boundary opt-in until its DB migration exists."""

    return os.environ.get(ARTIFACT_EXPORT_DURABILITY_SHADOW_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _validate_shadow_durable_publication_candidate(
    *,
    claim: object,
    reference: object,
    tenant_id: str,
    export_format: str,
) -> None:
    """Fail closed for the future ArtifactRef promotion port without publishing.

    This intentionally validates only identity already available at the handler
    boundary. It does not replace the existing workspace publication authority,
    create a local-path fallback, or claim that lifecycle/checkpoint/GC state has
    been migrated.
    """

    expected_kind = f"presentation-{export_format}"
    if getattr(claim, "fencing_token", 0) < 1:
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_fence_invalid")
    attempt_token = getattr(claim, "attempt_token", None)
    if not isinstance(attempt_token, str) or not attempt_token:
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_attempt_invalid")
    if getattr(reference, "owner_id", None) != getattr(claim, "owner_id", None):
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_owner_mismatch")
    if getattr(reference, "tenant_id", None) != tenant_id:
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_tenant_mismatch")
    if getattr(reference, "created_by_operation_id", None) != getattr(claim, "operation_id", None):
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_operation_mismatch")
    if getattr(reference, "kind", None) != expected_kind:
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_kind_invalid")
    digest = getattr(reference, "sha256", None)
    if not isinstance(digest, str) or len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_digest_invalid")
    size_bytes = getattr(reference, "size_bytes", None)
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
        raise NonRetryableWorkspaceOperationError("artifact_export_shadow_size_invalid")


def _stream_sha256_and_size(source: BinaryIO) -> tuple[str, int]:
    """Hash a compatibility spool without ever materializing it as bytes."""

    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := source.read(1024 * 1024):
        if not isinstance(chunk, bytes):
            raise PresentationExportContractError("public_export_stream_chunk_invalid")
        digest.update(chunk)
        size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes


def _path_sha256_and_size(path: Path) -> tuple[str, int]:
    with path.open("rb") as source:
        return _stream_sha256_and_size(source)


def _stage_public_artifact_compatibility_spool(
    source: BinaryIO,
    *,
    operation_id: object,
    artifact_id: object,
    export_format: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> Path:
    """Stream an immutable ArtifactRef into the legacy workspace spool.

    The file is only a compatibility input for the existing workspace
    publication service. The caller retains the ArtifactRef as the durable
    identity and verifies the object while copying; this helper never accepts
    a full in-memory export payload or treats a local path as the authority.
    """

    root = Path(get_app_data_directory_env()).resolve()
    target = root / "public-artifact-cache" / str(operation_id) / f"{artifact_id}.{export_format}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("rb") as existing:
            existing_digest, existing_size = _stream_sha256_and_size(existing)
        if existing_digest != expected_sha256 or existing_size != expected_size_bytes:
            raise PresentationExportContractError("public_export_cache_conflict")
        return target
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            digest = hashlib.sha256()
            size_bytes = 0
            while chunk := source.read(1024 * 1024):
                if not isinstance(chunk, bytes):
                    raise PresentationExportContractError("public_export_stream_chunk_invalid")
                digest.update(chunk)
                size_bytes += len(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest.hexdigest() != expected_sha256 or size_bytes != expected_size_bytes:
            raise PresentationExportContractError("presentation_export_artifact_digest_mismatch")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


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
        async with self._session_factory() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if presentation.revision != claim.expected_revision:
                raise NonRetryableWorkspaceOperationError("stale_presentation_revision")
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
            if (
                authored_vnext_projection is not None
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
                or operation.owner_scope != claim.owner_scope
            ):
                raise NonRetryableWorkspaceOperationError("workspace_export_operation_invalid")

        await context.progress(stage="export_rendering", percent=10)
        exported_source: Path | None = None
        execution = None
        native_export = None
        native_export_attempt_id = None
        direct_export_selected_adapter: str | None = None
        public_artifact_id = None
        quality_receipt_digest: str | None = None
        authored_quality_gate: object | None = None
        authored_scene_digest: str | None = None
        public_quality_receipt: dict[str, object] | None = None
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
                        "source_revision": claim.expected_revision,
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
                        source_revision=claim.expected_revision,
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
                        "source_revision": claim.expected_revision,
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
                    admission = await session.scalar(
                        select(PublicOperationAdmission).where(
                            PublicOperationAdmission.operation_id == claim.operation_id
                        )
                    )
                    if admission is None or admission.state != "active":
                        raise NonRetryableWorkspaceOperationError(
                            "public_export_admission_required"
                        )
                    tenant_id = admission.tenant_id
                    quality_receipt_digest = _validated_authored_quality_receipt_digest(
                        presentation.theme
                    )
                    authored_quality_gate = presentation.theme.get("authoredQualityGate")
                    if isinstance(authored_vnext_projection, Mapping):
                        raw_scene_digest = authored_vnext_projection.get("sceneDigest")
                        if isinstance(raw_scene_digest, str):
                            authored_scene_digest = raw_scene_digest
                    if (
                        authored_scene_digest is None
                        or not authored_scene_digest.startswith("sha256:")
                    ):
                        raise NonRetryableWorkspaceOperationError(
                            "public_quality_projection_inputs_missing"
                        )
                htmls = [slide.html_content for slide in slides]
                if not htmls or any(not isinstance(html, str) or not html for html in htmls):
                    raise NonRetryableWorkspaceOperationError(
                        "public_isolated_authored_html_required"
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
                        "network_none_worker": True,
                        "sessionless_capability": True,
                        "pptx_mode": "fidelity" if export_format == "pptx" else None,
                    },
                    "fallback_reason": None,
                    "asset_digests": {},
                    "compiler_runtime": {
                        "compiler": "public-isolated-export",
                        "compiler_version": "1",
                        "runtime": "render-export-worker",
                        "runtime_version": "public-worker-protocol/v1",
                    },
                    "freshness": {
                        "source_revision": claim.expected_revision,
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
                        export_format=cast(Literal["pptx", "pdf"], export_format),
                        source_revision=claim.expected_revision,
                        source_snapshot_sha256=source_snapshot_sha256,
                        request_hash=operation.request_hash,
                        idempotency_key_hash=operation.idempotency_key_hash,
                        requested_adapter="general",
                        provenance=initial_provenance,
                    )
                export_attempt_id = attempt.id
                native_export_attempt_id = attempt.id
                direct_export_selected_adapter = "general"
                public_ref = await render_and_export_authored_htmls(
                    claim,
                    tenant_id=tenant_id,
                    htmls=cast(list[str], htmls),
                    export_format=export_format,
                    scene_digest=(authored_scene_digest or ""),
                    quality_digest=quality_receipt_digest,
                    session_factory=self._session_factory,
                )
                if _artifact_export_durability_shadow_enabled():
                    _validate_shadow_durable_publication_candidate(
                        claim=claim,
                        reference=public_ref,
                        tenant_id=tenant_id,
                        export_format=export_format,
                    )
                public_artifact_id = public_ref.artifact_id
                artifact_service = configured_public_artifact_service()
                public_stream = await artifact_service.open_read_stream(public_ref)
                try:
                    if public_stream.content_length != public_ref.size_bytes:
                        raise PresentationExportContractError(
                            "presentation_export_artifact_size_mismatch"
                        )
                    export_path = await asyncio.to_thread(
                        _stage_public_artifact_compatibility_spool,
                        public_stream.body,
                        operation_id=claim.operation_id,
                        artifact_id=public_ref.artifact_id,
                        export_format=export_format,
                        expected_sha256=public_ref.sha256,
                        expected_size_bytes=public_ref.size_bytes,
                    )
                finally:
                    public_stream.close()
                exported_source = export_path
                artifact_sha256 = public_ref.sha256
                artifact_size_bytes = public_ref.size_bytes
                selected_adapter = "general"
                export_provenance = {
                    **initial_provenance,
                    "freshness": {
                        **initial_provenance["freshness"],
                        "package_sha256": artifact_sha256,
                        "package_digest_match": True,
                    },
                }
                if (
                    authored_quality_gate is None
                    or authored_scene_digest is None
                    or not authored_scene_digest.startswith("sha256:")
                ):
                    raise NonRetryableWorkspaceOperationError(
                        "public_quality_projection_inputs_missing"
                    )
                public_quality_receipt = project_persisted_authored_quality_receipt_v3(
                    authored_quality_gate=authored_quality_gate,
                    quality_receipt_id=f"quality:{claim.operation_id}:{public_ref.artifact_id}",
                    operation_id=str(claim.operation_id),
                    presentation_id=str(claim.presentation_id),
                    artifact_digest=f"sha256:{public_ref.sha256}",
                    source_revision=claim.expected_revision,
                    scene_digest=authored_scene_digest,
                    exporter_version="public-worker-protocol/v1",
                    evaluated_at=datetime.now(timezone.utc),
                ).model_dump(mode="json", by_alias=True)
            staged_digest, staged_size = await asyncio.to_thread(
                _path_sha256_and_size,
                export_path,
            )
            if staged_digest != artifact_sha256 or staged_size != artifact_size_bytes:
                raise PresentationExportContractError(
                    "presentation_export_artifact_digest_mismatch"
                )
            await context.raise_if_cancelled()
            await context.progress(stage="export_publishing", percent=80)
            async with self._session_factory() as session:
                artifact = await prepare_workspace_generation_artifact(
                    session,
                    source_path=str(export_path),
                    operation_id=claim.operation_id,
                    presentation_id=claim.presentation_id,
                    source_revision=claim.expected_revision,
                    kind=claim.kind,
                    export_attempt_id=export_attempt_id,
                    expected_source_sha256=artifact_sha256,
                    expected_source_size_bytes=artifact_size_bytes,
                )
                if public_artifact_id is not None:
                    workspace_artifact = await session.get(
                        WorkspaceArtifact,
                        artifact.artifact_id,
                    )
                    if workspace_artifact is None:
                        raise WorkspaceArtifactPublicationError(
                            "workspace_artifact_intent_conflict"
                        )
                    workspace_artifact.public_artifact_id = public_artifact_id
                    session.add(workspace_artifact)
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
                    public_artifact_id=public_artifact_id,
                    public_tenant_id=(tenant_id if public_artifact_id is not None else None),
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
            raise RetryableWorkspaceOperationError("presentation_export_failed") from error
        finally:
            # The renderer output is only a staging source. The immutable,
            # hashed workspace artifact is the durable copy and must be the
            # sole downloadable file after prepare succeeds or fails.
            if exported_source is not None:
                try:
                    exported_source.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Unable to remove workspace export staging source",
                        extra={
                            "operation_id": str(claim.operation_id),
                            "presentation_id": str(claim.presentation_id),
                            "format": export_format,
                        },
                        exc_info=True,
                    )

        await context.progress(stage="export_completed", percent=100)
        # Publication has already terminalized the operation. The runner will
        # reconcile this canonical durable projection rather than append twice.
        return WorkspaceOperationResult(
            payload={"status": "completed", "format": export_format},
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
