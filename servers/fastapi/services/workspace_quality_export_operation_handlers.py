"""Quality review and immutable export workspace operations."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.auth.internal import authenticated_internal_request_headers
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
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
from services.presentation_export_router import execute_presentation_export
from services.workspace_export_progress_poller import (
    start_export_progress_polling,
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

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS = 30.0


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
            if authored_vnext_projection is not None:
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
        try:
            if authored_vnext_projection is not None and export_format == "pptx":
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
                # Browser-backed exports alone need an ephemeral internal
                # credential. Native master export stays document-server-free.
                headers = await self._internal_header_provider()
                cookie_header = headers.get("Cookie") or None
                # The renderer runs in Next.js behind one blocking request, so
                # without this poller the operation reports nothing for the
                # minutes that render actually takes.
                stop_progress_polling = start_export_progress_polling(
                    operation_id=claim.operation_id,
                    cookie_header=cookie_header,
                    report=context.progress,
                )
                try:
                    async with self._session_factory() as session:
                        execution = await execute_presentation_export(
                            session,
                            render_presentation_id=claim.presentation_id,
                            attempt_presentation_id=claim.presentation_id,
                            export_format=cast(Literal["pptx", "pdf"], export_format),
                            expected_render_revision=claim.expected_revision,
                            source_revision=claim.expected_revision,
                            title=title,
                            cookie_header=cookie_header,
                            owner_id=claim.owner_id,
                            owner_scope=claim.owner_scope,
                            workspace_operation_id=claim.operation_id,
                            request_hash=operation.request_hash,
                            idempotency_key_hash=operation.idempotency_key_hash,
                            pptx_mode=pptx_mode,
                            font_embedding=font_embedding,
                        )
                finally:
                    await stop_progress_polling()
                exported = execution.presentation
                export_path = Path(exported.path)
                exported_source = export_path
                artifact_sha256 = execution.route.result.artifact_sha256
                artifact_size_bytes = execution.route.result.artifact_size_bytes
                source_snapshot_sha256 = execution.source_snapshot_sha256
                selected_adapter = execution.route.result.selected_adapter
                export_provenance = execution.provenance
                export_attempt_id = execution.attempt_id
            if artifact_sha256 != hashlib.sha256(export_path.read_bytes()).hexdigest():
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
                        selected_adapter="native_reconstructed",
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
                        selected_adapter="native_reconstructed",
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
                        selected_adapter="native_reconstructed",
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
                        selected_adapter="native_reconstructed",
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
                        selected_adapter="native_reconstructed",
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
