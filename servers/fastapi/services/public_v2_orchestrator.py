"""PostgreSQL-backed adapter for the canonical public v2 API.

The adapter only admits durable work and projects durable state. Provider,
render, and export execution remains owned by the workspace worker boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
import inspect
import json
from typing import Any, Iterator
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v2.contracts import (
    AuthorizedDownloadGrant,
    CancelOperationReceiptV2,
    GenerationRouteReceiptV1,
    OperationExecutionReceiptV2,
    OperationEventPageV2,
    OperationEventV2,
    OperationLinksV2,
    OperationReceiptV2,
    OperationStatusV2,
    PresentationExportRequestV2,
    PresentationGenerationRequestV2,
    PublicArtifactV2,
    PublicExportExecutionReceiptV1,
    PublicOperationErrorV2,
    QualityReceiptV3,
)
from api.v2.orchestration import (
    PublicRequestActor,
    PublicV2OrchestrationError,
)
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.public_artifact import PublicArtifact, PublicArtifactPublication
from models.sql.slide import SlideModel
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)
from models.workspace import WorkspaceBootstrapRequest
from services.artifact_store import (
    ArtifactNotFound,
    ArtifactStoreError,
    DurableArtifactService,
    configured_public_artifact_service,
)
from services.compatibility_migration.admission import (
    stamp_new_authored_presentation,
)
from api.v1.ppt.endpoints.presentation_generation_staging import (
    _durable_request_payload,
)
from services.public_operation_admission import (
    PrincipalContext,
    PublicAdmissionRejected,
    ProviderRequestPlan,
    WeightedResourcePlan,
    admit_public_operation_v2,
    plan_provider_requests,
    plan_weighted_resources,
)
from services.public_export_operation import (
    PublicExportOperationError,
    public_release_review_deck_render_identity_digest,
    public_release_review_runtime_binding,
    public_render_runtime_binding_digest,
)
from services.public_v2_operation_lifecycle import (
    PUBLIC_GENERATION_KIND as _GENERATION_KIND,
    PUBLIC_TERMINAL_STATES as _TERMINAL_STATES,
    aware_datetime as _aware,
    is_public_operation_authority,
    public_operation_phase as _phase,
    public_operation_status as _operation_status,
    public_progress_percent as _progress_percent,
)
from services.references.service import (
    ReferenceProviderError,
    admit_reference_snapshots,
    claim_reference_snapshots,
)
from services.workspace_operation_runner import (
    append_operation_event,
    request_workspace_operation_cancellation,
    wake_workspace_operation_runner,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from services.workspace_quality_operation_handler import (
    release_review_checker_policy_digest,
    validate_export_release_review_receipt,
    validate_release_review_base_receipts,
)
from services.workspace_db_adapter import reserve_conversation_positions
from services.workspace_service import (
    WorkspaceServiceError,
    bootstrap_workspace,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_tenant_id,
    reset_current_trace_id,
    set_current_owner_id,
    set_current_tenant_id,
    set_current_trace_id,
)


AdmissionCallable = Callable[..., Awaitable[PublicOperationAdmission]]
CancelCallable = Callable[..., Awaitable[bool]]
ArtifactServiceFactory = Callable[[], DurableArtifactService]
GenerationRouteResolver = Callable[
    ...,
    Awaitable[Mapping[str, Any] | GenerationRouteReceiptV1]
    | Mapping[str, Any]
    | GenerationRouteReceiptV1,
]

PUBLIC_V2_POSTGRESQL_STRICT_PROFILE = "postgresql-strict"
PUBLIC_V2_INTEGRATION_PROFILE_ENV = "PUBLIC_V2_INTEGRATION_PROFILE"
def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _generation_request_hash_payload(
    request: PresentationGenerationRequestV2,
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", by_alias=True)
    if "generation_mode" not in request.model_fields_set:
        payload.pop("generationMode", None)
        payload.pop("designPackPin", None)
    return payload


def _generation_route_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _installed_generation_route_resolver() -> GenerationRouteResolver | None:
    try:
        from services.authored_vnext.generation_route import (
            resolve_generation_route,
        )
    except ModuleNotFoundError as error:
        if error.name == "services.authored_vnext.generation_route":
            return None
        raise
    return resolve_generation_route


async def _resolve_generation_route_receipt(
    request: PresentationGenerationRequestV2,
    *,
    slide_count: int,
    resolver: GenerationRouteResolver | None,
) -> GenerationRouteReceiptV1:
    active_resolver = resolver or _installed_generation_route_resolver()
    if active_resolver is None:
        resolution: Mapping[str, Any] = {
            "selectedRoute": "authored-fallback",
            "narrativeIrDigest": None,
            "fallbackReason": "route_resolver_unavailable",
        }
    else:
        try:
            raw_resolution = active_resolver(
                generation_mode=request.generation_mode,
                design_pack_pin=(
                    request.design_pack_pin.model_dump(mode="json", by_alias=True)
                    if request.design_pack_pin is not None
                    else None
                ),
                source_mode=request.source_mode,
                authored_style=request.authored_style,
                slide_count=slide_count,
            )
            if inspect.isawaitable(raw_resolution):
                raw_resolution = await raw_resolution
        except Exception as error:
            raise _public_error(503, "generation_route_resolver_unavailable") from error
        if isinstance(raw_resolution, GenerationRouteReceiptV1):
            resolution = raw_resolution.model_dump(mode="json", by_alias=True)
        elif isinstance(raw_resolution, Mapping):
            resolution = raw_resolution
        else:
            raise _public_error(503, "generation_route_resolution_invalid")

    selected_route = resolution.get("selectedRoute") or resolution.get(
        "selected_route"
    )
    narrative_ir_digest = resolution.get("narrativeIrDigest") or resolution.get(
        "narrative_ir_digest"
    )
    fallback_reason = resolution.get("fallbackReason") or resolution.get(
        "fallback_reason"
    )
    resolved_design_pack_pin = resolution.get("designPackPin") or resolution.get(
        "design_pack_pin"
    )
    if selected_route == "authored-fallback" and not fallback_reason:
        fallback_reason = "resolver_selected_authored_fallback"
    receipt_payload: dict[str, Any] = {
        "schemaVersion": "generation-route-receipt/v1",
        "requestedMode": request.generation_mode,
        "selectedRoute": selected_route,
        "designPackPin": (
            resolved_design_pack_pin
            if isinstance(resolved_design_pack_pin, Mapping)
            else request.design_pack_pin.model_dump(mode="json", by_alias=True)
            if request.design_pack_pin is not None
            else None
        ),
        "narrativeIrDigest": narrative_ir_digest,
        "fallbackReason": fallback_reason,
        "outputCapability": "fidelity",
    }
    try:
        return GenerationRouteReceiptV1.model_validate(
            {
                **receipt_payload,
                "receiptDigest": _generation_route_digest(receipt_payload),
            }
        )
    except ValueError as error:
        raise _public_error(503, "generation_route_resolution_invalid") from error


def _effective_generation_engine(
    request: PresentationGenerationRequestV2,
) -> str:
    if request.generation_mode == "registered-template":
        # The design-pack route receipt and source-native handler own this
        # operation.  Sending it through bounded Deck IR preflight would reject
        # valid packs with more than ten requested output pages before the
        # provider-free capacity admission can run.
        return "authored"
    if "generation_mode" in request.model_fields_set:
        return "deck-ir"
    return request.generation_engine or "authored"


def _release_review_receipt_reusable(
    operation: WorkspaceOperation,
    *,
    theme: object,
    runtime_binding_digest: str,
    current_render_identity_digest: str,
    checker_policy_digest: str,
    match_result_revision: bool,
) -> bool:
    """Validate a succeeded review before it can suppress a replacement."""

    if (
        operation.state != "succeeded"
        or not isinstance(operation.expected_revision, int)
        or isinstance(operation.expected_revision, bool)
        or not isinstance(operation.result_revision, int)
        or isinstance(operation.result_revision, bool)
        or not isinstance(operation.result, Mapping)
    ):
        return False
    receipt = operation.result.get("release_review_receipt")
    if not isinstance(receipt, Mapping):
        return False
    try:
        validate_export_release_review_receipt(
            receipt,
            operation_id=operation.id,
            source_revision=operation.expected_revision,
            result_revision=operation.result_revision,
        )
        if receipt.get("baseEvidenceKind") == "generation-quality-v3":
            validate_release_review_base_receipts(
                theme,
                expected_deck_html_digest=str(
                    receipt.get("baseDeckHtmlDigest", "")
                ),
                expected_quality_receipt_digest=str(
                    receipt.get("baseQualityReceiptDigest", "")
                ),
            )
    except NonRetryableWorkspaceOperationError:
        return False
    identity_field = (
        "resultRenderIdentityDigest"
        if match_result_revision
        else "sourceRenderIdentityDigest"
    )
    return bool(
        receipt.get("renderRuntimeBindingDigest") == runtime_binding_digest
        and receipt.get("checkerPolicyDigest") == checker_policy_digest
        and receipt.get(identity_field) == current_render_identity_digest
    )


def _idempotency_hash(
    actor: PublicRequestActor,
    *,
    kind: str,
    key: str,
) -> str:
    return sha256(
        f"public-v2:{actor.tenant_id}:{actor.owner_id}:{kind}:{key}".encode("utf-8")
    ).hexdigest()


def resolve_public_v2_integration_profile(
    environ: Mapping[str, str],
) -> str | None:
    """Select the strict adapter only through an explicit launch profile."""

    configured = (environ.get(PUBLIC_V2_INTEGRATION_PROFILE_ENV) or "").strip()
    if not configured:
        return None
    if configured != PUBLIC_V2_POSTGRESQL_STRICT_PROFILE:
        raise RuntimeError("public_v2.integration_profile_invalid")
    return configured


def _public_error(
    status_code: int,
    reason_code: str,
    *,
    retry_after_seconds: int | None = None,
) -> PublicV2OrchestrationError:
    return PublicV2OrchestrationError(
        status_code=status_code,
        reason_code=reason_code,
        retry_after_seconds=retry_after_seconds,
    )


def _checkpoint_content_graph_authority(
    operation: WorkspaceOperation,
) -> dict[str, str | None]:
    request_payload = (
        operation.request_payload
        if isinstance(operation.request_payload, Mapping)
        else {}
    )
    preserve_checkpoint = request_payload.get(
        "preserve_source_checkpoint"
    ) or request_payload.get("preserveSourceCheckpoint")
    checkpoint = preserve_checkpoint or request_payload.get(
        "narrative_checkpoint"
    ) or request_payload.get("narrativeCheckpoint")
    if not isinstance(checkpoint, Mapping):
        raise ValueError("content_integrity_checkpoint_missing")
    checkpoint_operation_id = checkpoint.get("operationId") or checkpoint.get(
        "operation_id"
    )
    if str(checkpoint_operation_id or "") != str(operation.id):
        raise ValueError("content_integrity_checkpoint_operation_mismatch")
    if preserve_checkpoint is not None:
        preserve_input = checkpoint.get("preserveSourceInput")
        content_graph = (
            preserve_input.get("contentGraph")
            if isinstance(preserve_input, Mapping)
            else None
        )
        content_graph_digest = (
            content_graph.get("contentGraphDigest")
            if isinstance(content_graph, Mapping)
            else None
        )
        generation_lane = "preserve-source"
        attempt_binding_digest = checkpoint.get("attemptBindingDigest")
        preserve_source_input_digest = (
            preserve_input.get("inputDigest")
            if isinstance(preserve_input, Mapping)
            else None
        )
        narrative_digest = None
    else:
        narrative = checkpoint.get("narrative")
        if not isinstance(narrative, Mapping):
            raise ValueError("content_integrity_checkpoint_narrative_missing")
        content_graph_digest = narrative.get("contentGraphDigest") or narrative.get(
            "content_graph_digest"
        )
        generation_lane = "new-authoring"
        from services.authored_vnext.narrative_ir.validation import canonical_digest

        attempt_binding_digest = canonical_digest(
            {
                "operationId": str(checkpoint_operation_id),
                "jobId": str(checkpoint.get("jobId") or ""),
                "attemptId": str(checkpoint.get("attemptId") or ""),
                "fencingToken": checkpoint.get("fencingToken"),
            }
        )
        narrative_digest = narrative.get("narrativeDigest")
        preserve_source_input_digest = None
    if not isinstance(content_graph_digest, str) or not content_graph_digest:
        raise ValueError("content_integrity_checkpoint_graph_missing")
    provider_call_budget = checkpoint.get("providerCallBudget")
    if not isinstance(provider_call_budget, Mapping):
        raise ValueError("content_integrity_provider_budget_missing")
    from services.authored_vnext.narrative_ir.validation import canonical_digest

    provider_call_budget_digest = canonical_digest(dict(provider_call_budget))
    if not isinstance(attempt_binding_digest, str) or not attempt_binding_digest:
        raise ValueError("content_integrity_attempt_binding_missing")
    return {
        "generationLane": generation_lane,
        "contentGraphDigest": content_graph_digest,
        "attemptBindingDigest": attempt_binding_digest,
        "narrativeDigest": narrative_digest,
        "preserveSourceInputDigest": preserve_source_input_digest,
        "providerCallBudgetDigest": provider_call_budget_digest,
    }


def require_generation_content_integrity_for_export(
    operation: WorkspaceOperation,
    *,
    source_revision: int,
) -> None:
    """Fail closed when a registered-template revision lacks its integrity seal."""

    request_payload = (
        operation.request_payload
        if isinstance(operation.request_payload, Mapping)
        else {}
    )
    route = request_payload.get("generation_route_receipt")
    selected_route = (
        route.get("selectedRoute") or route.get("selected_route")
        if isinstance(route, Mapping)
        else None
    )
    if selected_route != "source-native":
        return
    if (
        operation.state != "succeeded"
        or operation.result_revision != source_revision
        or not isinstance(operation.result, Mapping)
    ):
        raise _public_error(409, "content_integrity_receipt_required")
    try:
        from services.authored_vnext.narrative_ir.content_graph import (
            validate_content_integrity_receipt_binding,
        )

        route_receipt = GenerationRouteReceiptV1.model_validate(route)
        raw_receipt = operation.result.get("contentIntegrityReceipt")
        authority = _checkpoint_content_graph_authority(operation)
        if (
            operation.expected_revision is None
            or operation.result_revision is None
        ):
            raise ValueError("content_integrity_binding_unavailable")
        validate_content_integrity_receipt_binding(
            raw_receipt,
            operation_id=operation.id,
            source_revision=int(operation.expected_revision),
            result_revision=int(operation.result_revision),
            generation_lane=str(authority["generationLane"]),
            content_graph_digest=str(authority["contentGraphDigest"]),
            route_strategy_receipt_digest=route_receipt.receipt_digest,
            attempt_binding_digest=str(authority["attemptBindingDigest"]),
            narrative_digest=authority["narrativeDigest"],
            preserve_source_input_digest=authority["preserveSourceInputDigest"],
            provider_call_budget_digest=str(authority["providerCallBudgetDigest"]),
        )
    except Exception as error:
        raise _public_error(409, "content_integrity_receipt_required") from error


def _translate_admission(error: PublicAdmissionRejected) -> PublicV2OrchestrationError:
    unavailable = {
        "public_postgresql_authority_required",
        "public_phase2_schema_unavailable",
        "public_operation_authority_unavailable",
        "public_quota_authority_unavailable",
    }
    if error.code in unavailable or error.code.endswith("_authority_unavailable"):
        return _public_error(503, "operation_authority_unavailable")
    retry_after = error.retry_after_seconds
    status_code = 429 if retry_after is not None or "limit_exceeded" in error.code else 409
    return _public_error(
        status_code,
        error.code,
        retry_after_seconds=retry_after,
    )


@contextmanager
def _actor_context(actor: PublicRequestActor) -> Iterator[None]:
    owner_token = set_current_owner_id(actor.owner_id)
    tenant_token = set_current_tenant_id(actor.tenant_id)
    trace_token = set_current_trace_id(actor.correlation_id)
    try:
        yield
    finally:
        reset_current_trace_id(trace_token)
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


def _operation_uuid(value: str, *, reason: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise _public_error(404, reason) from error


class SqlPublicV2Orchestrator:
    """Thin durable admission/projection adapter for public v2 routes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        admission: AdmissionCallable = admit_public_operation_v2,
        cancel: CancelCallable = request_workspace_operation_cancellation,
        artifact_service_factory: ArtifactServiceFactory = configured_public_artifact_service,
        generation_route_resolver: GenerationRouteResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._admission = admission
        self._cancel = cancel
        self._artifact_service_factory = artifact_service_factory
        self._generation_route_resolver = generation_route_resolver

    async def submit_generation(
        self,
        *,
        actor: PublicRequestActor,
        idempotency_key: str,
        request: PresentationGenerationRequestV2,
    ) -> OperationReceiptV2:
        with _actor_context(actor):
            try:
                receipt = await self._submit_generation_once(
                    actor=actor,
                    idempotency_key=idempotency_key,
                    request=request,
                )
            except IntegrityError:
                receipt = await self._replay_generation(
                    actor=actor,
                    idempotency_key=idempotency_key,
                    request=request,
                )
            except PublicAdmissionRejected as error:
                raise _translate_admission(error) from error
            except ReferenceProviderError as error:
                raise _public_error(error.status_code, error.code) from error
            except WorkspaceServiceError as error:
                raise _public_error(error.status_code, error.code) from error
            except SQLAlchemyError as error:
                raise _public_error(503, "operation_authority_unavailable") from error
        wake_workspace_operation_runner()
        return receipt

    async def _submit_generation_once(
        self,
        *,
        actor: PublicRequestActor,
        idempotency_key: str,
        request: PresentationGenerationRequestV2,
    ) -> OperationReceiptV2:
        kind = _GENERATION_KIND
        key_hash = _idempotency_hash(actor, kind=kind, key=idempotency_key)
        request_payload = _generation_request_hash_payload(request)
        request_hash = _canonical_hash(request_payload)
        async with self._session_factory() as session:
            existing = await self._operation_for_key(
                session,
                actor=actor,
                kind=kind,
                key_hash=key_hash,
            )
            if existing is not None:
                return await self._duplicate_receipt(
                    session,
                    existing,
                    actor=actor,
                    request_hash=request_hash,
                )

            reference_ids: list[uuid.UUID] = []
            for value in request.reference_snapshot_ids:
                try:
                    reference_ids.append(uuid.UUID(value))
                except ValueError as error:
                    raise _public_error(422, "reference_snapshot_id_invalid") from error
            snapshots = await admit_reference_snapshots(
                session,
                snapshot_ids=reference_ids,
            )
            asset_ids = list(
                dict.fromkeys(
                    binding.asset_id
                    for snapshot in snapshots
                    for binding in snapshot.asset_bindings
                )
            )
            slide_count = request.slide_count or (
                len(request.slides_markdown) if request.slides_markdown else 10
            )
            generation_route_receipt = await _resolve_generation_route_receipt(
                request,
                slide_count=slide_count,
                resolver=self._generation_route_resolver,
            )
            route_payload = generation_route_receipt.model_dump(
                mode="json",
                by_alias=True,
            )
            source_native_route = (
                generation_route_receipt.selected_route == "source-native"
            )
            effective_generation_engine = (
                "authored" if source_native_route else _effective_generation_engine(request)
            )
            internal_generation_mode = (
                "registered-template"
                if source_native_route
                else request.generation_mode
            )
            internal_design_pack_pin = (
                generation_route_receipt.design_pack_pin.model_dump(
                    mode="json", by_alias=True
                )
                if generation_route_receipt.design_pack_pin is not None
                else None
            )
            internal_request = GeneratePresentationRequest(
                content=request.content,
                slides_markdown=request.slides_markdown,
                instructions=request.instructions,
                n_slides=slide_count,
                language=request.language,
                template="authored",
                strategy="authored",
                qualityProfile=(
                    "release" if request.quality_policy == "release-v1" else "development"
                ),
                visionQa=request.quality_policy == "release-v1",
                reference_snapshot_ids=reference_ids,
                authored_style=request.authored_style,
                sourceMode=request.source_mode,
                sourceContentManifestPin=(
                    request.source_content_manifest_pin.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                    if request.source_content_manifest_pin is not None
                    else None
                ),
                contentPolicy=(
                    request.content_policy.model_dump(mode="json", by_alias=True)
                    if request.content_policy is not None
                    else None
                ),
                generationMode=internal_generation_mode,
                designPackPin=internal_design_pack_pin,
                generationEngine=effective_generation_engine,
            )
            # This has no provider side effect.  It must remain ahead of the
            # durable admission/reservation below so an operation is sealed as
            # deck-ir, legacy-authored, or reject before billable work exists.
            from services.authored_vnext.deck_ir.strategy import (
                capability_preflight,
                seal_generation_strategy_decision,
                server_owned_requirement_graph,
            )
            from services.authored_vnext.deck_ir.integration import (
                deck_ir_execution_adapter_configured,
                ensure_product_deck_ir_execution_adapter,
            )

            ensure_product_deck_ir_execution_adapter(
                explicit=effective_generation_engine == "deck-ir"
            )
            strategy_preflight = capability_preflight(
                request=internal_request,
                available_capabilities=(
                    (
                        "structured-deck-ir",
                        "constraint-compiler",
                        "fidelity-html-semantic",
                    )
                    if deck_ir_execution_adapter_configured()
                    else ()
                ),
            )
            generation_payload = _durable_request_payload(internal_request)
            generation_payload["generation_route_receipt"] = route_payload
            presentation_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"presenton:public-v2:{actor.owner_id}:{key_hash}",
            )
            presentation = PresentationModel(
                id=presentation_id,
                owner_id=actor.owner_id,
                content=request.content,
                n_slides=slide_count,
                language=(request.language or "").strip(),
                reference_snapshot_ids=[str(value) for value in reference_ids] or None,
                instructions=request.instructions,
                lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            )
            stamp_new_authored_presentation(presentation)
            session.add(presentation)
            await session.flush()
            conversation, _message, operation, replayed = await bootstrap_workspace(
                session,
                presentation_id=presentation_id,
                request=WorkspaceBootstrapRequest(
                    idempotency_key=f"public-v2:{key_hash}",
                    prompt=request.content,
                    generation_settings={
                        "n_slides": slide_count,
                        "language": request.language,
                        "instructions": request.instructions,
                        "reference_snapshot_ids": [str(value) for value in reference_ids],
                        "quality_policy": request.quality_policy,
                        "authored_style": request.authored_style,
                        "source_mode": request.source_mode,
                        "source_content_manifest_pin": (
                            request.source_content_manifest_pin.model_dump(
                                mode="json",
                                by_alias=True,
                            )
                            if request.source_content_manifest_pin is not None
                            else None
                        ),
                        "content_policy": (
                            request.content_policy.model_dump(
                                mode="json",
                                by_alias=True,
                            )
                            if request.content_policy is not None
                            else None
                        ),
                        "generation_mode": internal_generation_mode,
                        "design_pack_pin": internal_design_pack_pin,
                        "generation_engine": effective_generation_engine,
                    },
                    asset_ids=asset_ids,
                    operation_type=kind,
                    expected_revision=presentation.revision,
                ),
                commit=False,
                initial_operation_state="queued",
            )
            if replayed:
                return await self._duplicate_receipt(
                    session,
                    operation,
                    actor=actor,
                    request_hash=request_hash,
                )
            requirement_graph = server_owned_requirement_graph(internal_request.content)
            strategy_receipt = seal_generation_strategy_decision(
                operation_id=str(operation.id),
                requirement_graph_digest=str(requirement_graph["graphDigest"]),
                requested_profile="balanced-quality-v1",
                preflight=strategy_preflight,
                force_strategy=(
                    "reject"
                    if effective_generation_engine == "deck-ir"
                    and strategy_preflight.get("missingCapabilities")
                    else "deck-ir"
                    if effective_generation_engine == "deck-ir"
                    else "legacy-authored"
                    if effective_generation_engine == "authored"
                    else None
                ),
                requested_generation_engine=effective_generation_engine,
            )
            if strategy_receipt["selectedStrategy"] == "reject":
                raise _public_error(422, "generation_strategy_capability_missing")
            await claim_reference_snapshots(
                session,
                snapshot_ids=reference_ids,
                conversation_id=conversation.id,
            )
            async_status = AsyncPresentationGenerationTaskModel(
                owner_id=actor.owner_id,
                status="pending",
                message="Queued for generation",
                data=None,
            )
            job = PresentationGenerationJob(
                task_id=async_status.id,
                presentation_id=presentation_id,
                workspace_operation_id=operation.id,
                idempotency_key_hash=key_hash,
                request_id=actor.correlation_id,
                request_sha256=request_hash,
                request_payload=generation_payload,
                export_cookie_required=False,
                state="queued",
                total_slides=slide_count,
            )
            operation.idempotency_key_hash = key_hash
            operation.request_hash = request_hash
            operation.request_payload = {
                "schema_version": "public-generation-operation/v2",
                "generation_request": generation_payload,
                "task_id": async_status.id,
                "job_id": str(job.id),
                "quality_policy": request.quality_policy,
                "generation_strategy": strategy_receipt,
                "generation_route_receipt": route_payload,
            }
            operation.expected_revision = presentation.revision
            operation.next_attempt_at = get_current_utc_datetime()
            session.add_all((async_status, job, operation))
            await session.flush()
            admission = await self._admit(
                session,
                actor=actor,
                operation=operation,
                operation_kind="generation",
                request_plan=plan_provider_requests(
                    slide_count=slide_count,
                    image_budget=slide_count,
                    repair_budget=slide_count,
                    vqa_budget=(
                        slide_count if request.quality_policy == "release-v1" else 0
                    ),
                ),
                resource_plan=self._resource_plan(
                    slide_count=slide_count,
                    fidelity_mode="standard",
                ),
            )
            await session.commit()
            return self._receipt(operation, admission, duplicate=False)

    async def _replay_generation(
        self,
        *,
        actor: PublicRequestActor,
        idempotency_key: str,
        request: PresentationGenerationRequestV2,
    ) -> OperationReceiptV2:
        key_hash = _idempotency_hash(
            actor,
            kind=_GENERATION_KIND,
            key=idempotency_key,
        )
        request_hash = _canonical_hash(_generation_request_hash_payload(request))
        async with self._session_factory() as session:
            operation = await self._operation_for_key(
                session,
                actor=actor,
                kind=_GENERATION_KIND,
                key_hash=key_hash,
            )
            if operation is None:
                raise _public_error(409, "idempotency_key_conflict")
            return await self._duplicate_receipt(
                session,
                operation,
                actor=actor,
                request_hash=request_hash,
            )

    async def submit_export(
        self,
        *,
        actor: PublicRequestActor,
        presentation_id: str,
        idempotency_key: str,
        request: PresentationExportRequestV2,
    ) -> OperationReceiptV2:
        if (
            request.output.mode == "editable-hybrid"
            or request.output.required_capabilities
        ):
            raise _public_error(422, "capability_not_supported")
        with _actor_context(actor):
            try:
                receipt = await self._submit_export_once(
                    actor=actor,
                    presentation_id=presentation_id,
                    idempotency_key=idempotency_key,
                    request=request,
                )
            except IntegrityError:
                receipt = await self._replay_export(
                    actor=actor,
                    presentation_id=presentation_id,
                    idempotency_key=idempotency_key,
                    request=request,
                )
            except PublicAdmissionRejected as error:
                raise _translate_admission(error) from error
            except SQLAlchemyError as error:
                raise _public_error(503, "operation_authority_unavailable") from error
        wake_workspace_operation_runner()
        return receipt

    async def _submit_export_once(
        self,
        *,
        actor: PublicRequestActor,
        presentation_id: str,
        idempotency_key: str,
        request: PresentationExportRequestV2,
    ) -> OperationReceiptV2:
        target_id = _operation_uuid(presentation_id, reason="presentation_not_found")
        export_format = request.output.format
        kind = f"export.{export_format}"
        key_hash = _idempotency_hash(actor, kind=kind, key=idempotency_key)
        request_payload = {
            "presentationId": presentation_id,
            **request.model_dump(mode="json", by_alias=True),
        }
        request_hash = _canonical_hash(request_payload)
        async with self._session_factory() as session:
            existing = await self._operation_for_key(
                session,
                actor=actor,
                kind=kind,
                key_hash=key_hash,
                presentation_id=target_id,
            )
            if existing is not None:
                return await self._duplicate_receipt(
                    session,
                    existing,
                    actor=actor,
                    request_hash=request_hash,
                )
            # Presentation/workspace rows predate tenant columns.  Until those
            # legacy tables can be migrated without widening non-public flows,
            # the immutable public generation admission is the tenant binding
            # for a presentation entering the public export path.
            source_operation = await session.scalar(
                select(WorkspaceOperation)
                .join(
                    PublicOperationAdmission,
                    PublicOperationAdmission.operation_id == WorkspaceOperation.id,
                )
                .where(
                    WorkspaceOperation.presentation_id == target_id,
                    WorkspaceOperation.owner_id == actor.owner_id,
                    WorkspaceOperation.kind == "presentation.generate",
                    WorkspaceOperation.state == "succeeded",
                    WorkspaceOperation.result_revision == request.source_revision,
                    PublicOperationAdmission.owner_id == actor.owner_id,
                    PublicOperationAdmission.tenant_id == actor.tenant_id,
                    PublicOperationAdmission.operation_kind == "generation",
                )
                .order_by(WorkspaceOperation.updated_at.desc())
                .limit(1)
            )
            if source_operation is None:
                raise _public_error(404, "presentation_not_found")
            require_generation_content_integrity_for_export(
                source_operation,
                source_revision=request.source_revision,
            )
            presentation = await session.scalar(
                select(PresentationModel)
                .where(
                    PresentationModel.id == target_id,
                    PresentationModel.owner_id == actor.owner_id,
                )
                .with_for_update()
            )
            if presentation is None:
                raise _public_error(404, "presentation_not_found")
            if presentation.revision != request.source_revision:
                raise _public_error(409, "stale_revision")
            review_runtime_binding_digest: str | None = None
            current_review_render_identity_digest: str | None = None
            review_checker_policy_digest: str | None = None
            if request.quality_policy == "release-v1":
                slide_html_rows = await session.execute(
                    select(SlideModel.html_content)
                    .where(SlideModel.presentation == target_id)
                    .order_by(SlideModel.index, SlideModel.id)
                )
                source_htmls = list(slide_html_rows.scalars())
                try:
                    review_runtime_binding = public_release_review_runtime_binding()
                    review_runtime_binding_digest = (
                        public_render_runtime_binding_digest(review_runtime_binding)
                    )
                    current_review_render_identity_digest = (
                        public_release_review_deck_render_identity_digest(
                            source_htmls
                        )
                    )
                    review_checker_policy_digest = (
                        release_review_checker_policy_digest()
                    )
                except PublicExportOperationError as error:
                    raise _public_error(503, str(error)) from error
            conversation = await session.scalar(
                select(WorkspaceConversation).where(
                    WorkspaceConversation.owner_id == actor.owner_id,
                    WorkspaceConversation.presentation_id == target_id,
                )
            )
            if conversation is None:
                conversation = WorkspaceConversation(
                    owner_id=actor.owner_id,
                    owner_scope=str(actor.owner_id),
                    presentation_id=target_id,
                )
                session.add(conversation)
                await session.flush()
            now = get_current_utc_datetime()
            export_operation_id = uuid.uuid4()
            review_operation: WorkspaceOperation | None = None
            review_created = False
            review_reused_by_result = False
            review_key_hash: str | None = None
            review_payload: dict[str, Any] | None = None
            review_request_hash: str | None = None
            if request.quality_policy == "release-v1":
                assert review_runtime_binding_digest is not None
                assert current_review_render_identity_digest is not None
                assert review_checker_policy_digest is not None
                # PPTX and PDF from the same immutable revision consume one
                # sealed full-deck review. Keeping this identity format-neutral
                # also keeps the quality digest stable across both containers.
                review_identity = {
                    "schemaVersion": "public-export-release-review-identity/v2",
                    "tenantId": actor.tenant_id,
                    "ownerId": str(actor.owner_id),
                    "presentationId": str(target_id),
                    "sourceRevision": request.source_revision,
                    "qualityPolicy": request.quality_policy,
                    "renderRuntimeBindingDigest": review_runtime_binding_digest,
                    "sourceRenderIdentityDigest": (
                        current_review_render_identity_digest
                    ),
                    "checkerPolicyDigest": review_checker_policy_digest,
                }
                review_key_hash = _canonical_hash(review_identity)
                review_payload = {
                    "schema_version": "public-export-release-review/v2",
                    "command": "Run release quality review before export",
                    "command_type": "quality.review",
                    "source_revision": request.source_revision,
                    "context": {
                        "mode": "analyze_and_fix",
                        "scope": "all",
                        "slide_indices": [],
                        "slidespec_opt_in": True,
                        "release_policy": request.quality_policy,
                        "render_runtime_binding_digest": (
                            review_runtime_binding_digest
                        ),
                        "source_render_identity_digest": (
                            current_review_render_identity_digest
                        ),
                        "checker_policy_digest": review_checker_policy_digest,
                    },
                }
                review_request_hash = _canonical_hash(review_payload)
                review_operation = await session.scalar(
                    select(WorkspaceOperation).where(
                        WorkspaceOperation.owner_id == actor.owner_id,
                        WorkspaceOperation.presentation_id == target_id,
                        WorkspaceOperation.kind == "quality.review",
                        WorkspaceOperation.idempotency_key_hash == review_key_hash,
                    )
                )
                reusable_review_states = {
                    "queued",
                    "waiting_for_assets",
                    "blocked",
                    "running",
                    "approval_required",
                    "awaiting_approval",
                    "cancelling",
                    "succeeded",
                }
                terminal_review: WorkspaceOperation | None = None
                if review_operation is not None:
                    direct_context = review_operation.request_payload.get("context")
                    review_admission = await session.scalar(
                        select(PublicOperationAdmission).where(
                            PublicOperationAdmission.operation_id
                            == review_operation.id,
                            PublicOperationAdmission.owner_id == actor.owner_id,
                            PublicOperationAdmission.tenant_id == actor.tenant_id,
                            PublicOperationAdmission.operation_kind == "generation",
                        )
                    )
                    if review_admission is None:
                        raise _public_error(503, "operation_authority_unavailable")
                    if (
                        review_operation.state == "succeeded"
                        and review_admission.state
                        not in {"queued", "active", "succeeded"}
                    ) or (
                        review_operation.state in reusable_review_states - {"succeeded"}
                        and review_admission.state not in {"queued", "active"}
                    ):
                        raise _public_error(503, "operation_authority_unavailable")
                    if review_operation.state not in reusable_review_states:
                        terminal_review = review_operation
                        review_operation = None
                    elif (
                        not isinstance(direct_context, Mapping)
                        or direct_context.get("release_policy")
                        != request.quality_policy
                        or direct_context.get("render_runtime_binding_digest")
                        != review_runtime_binding_digest
                        or direct_context.get("source_render_identity_digest")
                        != current_review_render_identity_digest
                        or direct_context.get("checker_policy_digest")
                        != review_checker_policy_digest
                    ):
                        terminal_review = review_operation
                        review_operation = None
                    elif review_operation.state == "succeeded" and not (
                        _release_review_receipt_reusable(
                            review_operation,
                            theme=presentation.theme,
                            runtime_binding_digest=review_runtime_binding_digest,
                            current_render_identity_digest=(
                                current_review_render_identity_digest
                            ),
                            checker_policy_digest=review_checker_policy_digest,
                            match_result_revision=False,
                        )
                    ):
                        terminal_review = review_operation
                        review_operation = None
                if review_operation is None:
                    # Recompute the newest terminal link from the ordered set;
                    # this advances a replacement chain after more than one
                    # failed execution instead of reusing the first hash.
                    direct_terminal_review = terminal_review
                    terminal_review = None
                    candidates = list(
                        (
                            await session.scalars(
                                select(WorkspaceOperation)
                                .where(
                                    WorkspaceOperation.owner_id == actor.owner_id,
                                    WorkspaceOperation.presentation_id == target_id,
                                    WorkspaceOperation.kind == "quality.review",
                                )
                                .order_by(
                                    WorkspaceOperation.completed_at.desc(),
                                    WorkspaceOperation.created_at.desc(),
                                )
                                .limit(32)
                            )
                        ).all()
                    )
                    tenant_candidates: list[
                        tuple[WorkspaceOperation, PublicOperationAdmission]
                    ] = []
                    for candidate in candidates:
                        candidate_context = candidate.request_payload.get("context")
                        candidate_admission = await session.scalar(
                            select(PublicOperationAdmission).where(
                                PublicOperationAdmission.operation_id == candidate.id,
                                PublicOperationAdmission.owner_id == actor.owner_id,
                                PublicOperationAdmission.tenant_id == actor.tenant_id,
                                PublicOperationAdmission.operation_kind == "generation",
                            )
                        )
                        context_runtime_matches = bool(
                            candidate_admission is not None
                            and isinstance(candidate_context, Mapping)
                            and candidate_context.get("release_policy")
                            == request.quality_policy
                            and candidate_context.get(
                                "render_runtime_binding_digest"
                            )
                            == review_runtime_binding_digest
                            and candidate_context.get("checker_policy_digest")
                            == review_checker_policy_digest
                            and isinstance(candidate.expected_revision, int)
                            and not isinstance(candidate.expected_revision, bool)
                        )
                        if context_runtime_matches:
                            assert candidate_admission is not None
                            assert isinstance(candidate_context, Mapping)
                            tenant_candidates.append((candidate, candidate_admission))
                            exact_source_identity = bool(
                                candidate.expected_revision == request.source_revision
                                and candidate_context.get(
                                    "source_render_identity_digest"
                                )
                                == current_review_render_identity_digest
                            )
                            if exact_source_identity and candidate.state in reusable_review_states:
                                succeeded_reusable = bool(
                                    candidate.state == "succeeded"
                                    and candidate_admission.state
                                    in {"active", "succeeded"}
                                    and _release_review_receipt_reusable(
                                        candidate,
                                        theme=presentation.theme,
                                        runtime_binding_digest=(
                                            review_runtime_binding_digest
                                        ),
                                        current_render_identity_digest=(
                                            current_review_render_identity_digest
                                        ),
                                        checker_policy_digest=(
                                            review_checker_policy_digest
                                        ),
                                        match_result_revision=False,
                                    )
                                )
                                pending_reusable = bool(
                                    candidate.state != "succeeded"
                                    and candidate_admission.state
                                    in {"queued", "active"}
                                )
                                if succeeded_reusable or pending_reusable:
                                    review_operation = candidate
                                    break
                                if terminal_review is None:
                                    terminal_review = candidate
                            if (
                                terminal_review is None
                                and exact_source_identity
                                and candidate.state not in reusable_review_states
                            ):
                                terminal_review = candidate
                    if terminal_review is None:
                        terminal_review = direct_terminal_review
                    if review_operation is None:
                        for candidate, candidate_admission in tenant_candidates:
                            if (
                                candidate.state == "succeeded"
                                and candidate_admission.state
                                in {"active", "succeeded"}
                                and candidate.result_revision
                                == request.source_revision
                                and _release_review_receipt_reusable(
                                    candidate,
                                    theme=presentation.theme,
                                    runtime_binding_digest=(
                                        review_runtime_binding_digest
                                    ),
                                    current_render_identity_digest=(
                                        current_review_render_identity_digest
                                    ),
                                    checker_policy_digest=(
                                        review_checker_policy_digest
                                    ),
                                    match_result_revision=True,
                                )
                            ):
                                review_operation = candidate
                                review_reused_by_result = True
                                break
                    if review_operation is None and terminal_review is not None:
                        # A terminal execution/admission cannot be reactivated.
                        # Chain a deterministic replacement from the newest
                        # failed review; the locked presentation serializes
                        # concurrent submitters onto the same replacement.
                        review_key_hash = _canonical_hash(
                            {
                                **review_identity,
                                "replacesOperationId": str(terminal_review.id),
                            }
                        )
                review_created = review_operation is None
            reservation = await reserve_conversation_positions(
                session,
                conversation.id,
                2 if review_created else 1,
            )
            if request.quality_policy == "release-v1" and review_operation is None:
                assert review_payload is not None
                assert review_request_hash is not None
                assert review_key_hash is not None
                review_operation = WorkspaceOperation(
                    owner_id=actor.owner_id,
                    owner_scope=str(actor.owner_id),
                    conversation_id=conversation.id,
                    presentation_id=target_id,
                    anchor_position=reservation.start,
                    kind="quality.review",
                    state="queued",
                    idempotency_key_hash=review_key_hash,
                    request_hash=review_request_hash,
                    request_payload=review_payload,
                    expected_revision=request.source_revision,
                    next_attempt_at=now,
                )
            elif request.quality_policy == "release-v1" and (
                review_operation is None
                or review_request_hash is None
                or review_operation.owner_scope != str(actor.owner_id)
                or (
                    not review_reused_by_result
                    and (
                        review_operation.expected_revision != request.source_revision
                        or review_operation.request_hash != review_request_hash
                    )
                )
            ):
                raise _public_error(409, "idempotency_conflict")
            export_payload: dict[str, Any] = {
                "schema_version": "public-export-operation/v2",
                "command": f"Export presentation as {export_format}",
                "command_type": kind,
                "source_revision": request.source_revision,
                "quality_policy": request.quality_policy,
                "context": {
                    "pptx_mode": (
                        "hybrid"
                        if request.output.mode == "editable-hybrid"
                        else "fidelity"
                    ),
                    "font_embedding": request.output.font_embedding == "subset",
                    "fallback_policy": request.output.fallback_policy,
                    "required_capabilities": request.output.required_capabilities,
                },
            }
            if review_operation is not None:
                export_payload.update(
                    release_review_operation_id=str(review_operation.id),
                    reviewed_source_revision=review_operation.expected_revision,
                )
            operation = WorkspaceOperation(
                id=export_operation_id,
                owner_id=actor.owner_id,
                owner_scope=str(actor.owner_id),
                conversation_id=conversation.id,
                presentation_id=target_id,
                anchor_position=reservation.start + (1 if review_created else 0),
                kind=kind,
                state="queued",
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                request_payload=export_payload,
                expected_revision=request.source_revision,
                # The review is the durable predecessor. Its earlier anchor and
                # immediate readiness normally claim first; this short delay is
                # only a starvation guard for workers that observed both rows
                # in the same scheduling tick.
                next_attempt_at=(
                    now + timedelta(seconds=1)
                    if review_operation is not None
                    else now
                ),
            )
            if review_created:
                assert review_operation is not None
                session.add(review_operation)
            session.add(operation)
            await session.flush()
            if review_created:
                assert review_operation is not None
                await self._admit(
                    session,
                    actor=actor,
                    operation=review_operation,
                    # The predecessor performs bounded provider critique/VQA
                    # calls, so its admission must use the provider-bearing
                    # generation class.  The child export keeps the separate
                    # export/Chromium resource admission below.
                    operation_kind="generation",
                    request_plan=plan_provider_requests(
                        slide_count=max(1, presentation.n_slides),
                        image_budget=0,
                        repair_budget=max(1, presentation.n_slides),
                        vqa_budget=max(1, presentation.n_slides),
                    ),
                    resource_plan=self._resource_plan(
                        slide_count=max(1, presentation.n_slides),
                        fidelity_mode="fidelity",
                    ),
                )
                await append_operation_event(
                    session,
                    operation_id=review_operation.id,
                    event_type="queued",
                    payload={
                        "state": "queued",
                        "phase": "release_quality_review",
                        "parent_operation_id": str(operation.id),
                    },
                    operation_kind="quality.review",
                    allowed_states=("queued",),
                )
            admission = await self._admit(
                session,
                actor=actor,
                operation=operation,
                operation_kind="export",
                request_plan=None,
                resource_plan=self._resource_plan(
                    slide_count=max(1, presentation.n_slides),
                    fidelity_mode=(
                        "hybrid"
                        if request.output.mode == "editable-hybrid"
                        else "fidelity"
                    ),
                ),
            )
            await append_operation_event(
                session,
                operation_id=operation.id,
                event_type="queued",
                payload={"state": "queued", "phase": "admission"},
                operation_kind=kind,
                allowed_states=("queued",),
            )
            await session.commit()
            return self._receipt(operation, admission, duplicate=False)

    async def _replay_export(
        self,
        *,
        actor: PublicRequestActor,
        presentation_id: str,
        idempotency_key: str,
        request: PresentationExportRequestV2,
    ) -> OperationReceiptV2:
        target_id = _operation_uuid(presentation_id, reason="presentation_not_found")
        kind = f"export.{request.output.format}"
        key_hash = _idempotency_hash(actor, kind=kind, key=idempotency_key)
        request_hash = _canonical_hash(
            {
                "presentationId": presentation_id,
                **request.model_dump(mode="json", by_alias=True),
            }
        )
        async with self._session_factory() as session:
            operation = await self._operation_for_key(
                session,
                actor=actor,
                kind=kind,
                key_hash=key_hash,
                presentation_id=target_id,
            )
            if operation is None:
                raise _public_error(409, "idempotency_key_conflict")
            return await self._duplicate_receipt(
                session,
                operation,
                actor=actor,
                request_hash=request_hash,
            )

    async def get_operation(
        self,
        *,
        actor: PublicRequestActor,
        operation_id: str,
    ) -> OperationStatusV2:
        with _actor_context(actor):
            try:
                async with self._session_factory() as session:
                    operation, admission = await self._public_operation(
                        session,
                        actor=actor,
                        operation_id=operation_id,
                    )
                    artifacts = await self._artifacts(session, actor, operation)
                    return self._status(operation, admission, artifacts)
            except SQLAlchemyError as error:
                raise _public_error(503, "operation_authority_unavailable") from error

    async def list_operation_events(
        self,
        *,
        actor: PublicRequestActor,
        operation_id: str,
        after: str | None,
        limit: int,
    ) -> OperationEventPageV2:
        with _actor_context(actor):
            try:
                after_cursor = int(after) if after is not None else None
            except ValueError as error:
                raise _public_error(422, "operation_event_cursor_invalid") from error
            try:
                async with self._session_factory() as session:
                    operation, _admission = await self._public_operation(
                        session,
                        actor=actor,
                        operation_id=operation_id,
                    )
                    statement = select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation.id,
                        WorkspaceOperationEvent.owner_id == actor.owner_id,
                    )
                    if after_cursor is not None:
                        statement = statement.where(
                            WorkspaceOperationEvent.cursor > after_cursor
                        )
                    rows = list(
                        (
                            await session.scalars(
                                statement.order_by(WorkspaceOperationEvent.cursor).limit(limit)
                            )
                        ).all()
                    )
                    events = [self._event(operation, row) for row in rows]
                    # The public client persists this opaque cursor across a
                    # reload. Returning it for every non-empty page (not only
                    # a full page) prevents a short initial history from being
                    # replayed forever on each status poll.
                    next_cursor = (
                        str(rows[-1].cursor)
                        if rows and rows[-1].cursor is not None
                        else None
                    )
                    return OperationEventPageV2(
                        operation_id=str(operation.id),
                        events=events,
                        next_cursor=next_cursor,
                    )
            except SQLAlchemyError as error:
                raise _public_error(503, "operation_authority_unavailable") from error

    async def cancel_operation(
        self,
        *,
        actor: PublicRequestActor,
        operation_id: str,
    ) -> CancelOperationReceiptV2:
        with _actor_context(actor):
            operation_uuid = _operation_uuid(
                operation_id,
                reason="operation_not_found",
            )
            try:
                async with self._session_factory() as session:
                    operation, _admission = await self._public_operation(
                        session,
                        actor=actor,
                        operation_id=operation_id,
                    )
                    before_terminal = operation.state in _TERMINAL_STATES
                requested = await self._cancel(
                    operation_uuid,
                    expected_version=operation.version,
                    session_factory=self._session_factory,
                )
                async with self._session_factory() as session:
                    operation, _admission = await self._public_operation(
                        session,
                        actor=actor,
                        operation_id=operation_id,
                    )
                return CancelOperationReceiptV2(
                    operation_id=str(operation.id),
                    status=_operation_status(operation.state),
                    cancellation_requested=(requested or operation.state == "cancelled")
                    and not before_terminal,
                    requested_at=_aware(
                        operation.cancel_requested_at or get_current_utc_datetime()
                    ),
                )
            except PublicAdmissionRejected as error:
                raise _translate_admission(error) from error
            except SQLAlchemyError as error:
                raise _public_error(503, "operation_authority_unavailable") from error

    async def authorize_download(
        self,
        *,
        actor: PublicRequestActor,
        artifact_id: str,
    ) -> AuthorizedDownloadGrant:
        with _actor_context(actor):
            artifact_uuid = _operation_uuid(
                artifact_id,
                reason="artifact_not_found",
            )
            try:
                async with self._session_factory() as session:
                    publication_row = (
                        await session.execute(
                            select(PublicArtifact, PublicArtifactPublication)
                            .join(
                                PublicArtifactPublication,
                                PublicArtifactPublication.artifact_id
                                == PublicArtifact.id,
                            )
                            .where(
                                PublicArtifact.id == artifact_uuid,
                                PublicArtifact.owner_id == actor.owner_id,
                                PublicArtifact.tenant_id == actor.tenant_id,
                                PublicArtifact.lifecycle_state == "committed",
                                PublicArtifact.created_by_operation_id
                                == PublicArtifactPublication.operation_id,
                            )
                            .order_by(PublicArtifactPublication.published_at.desc())
                            .limit(1)
                        )
                    ).one_or_none()
                    if publication_row is None:
                        raise _public_error(404, "artifact_not_found")
                    artifact, publication = publication_row
                    operation, _admission = await self._public_operation(
                        session,
                        actor=actor,
                        operation_id=str(artifact.created_by_operation_id),
                    )
                    if (
                        operation.state != "succeeded"
                        or operation.result_revision is None
                        or publication.operation_id != operation.id
                        or publication.revision != operation.result_revision
                        or publication.fencing_token != operation.fencing_token
                    ):
                        raise _public_error(404, "artifact_not_found")
                    reference = artifact.to_ref()
                service = self._artifact_service_factory()
                grant = await service.create_signed_download(
                    reference,
                    expires_seconds=180,
                )
                return AuthorizedDownloadGrant(
                    url=grant.url,
                    issued_at=_aware(grant.expires_at) - timedelta(seconds=180),
                    expires_at=_aware(grant.expires_at),
                    etag=grant.etag,
                )
            except PublicV2OrchestrationError:
                raise
            except (ArtifactNotFound, ArtifactStoreError) as error:
                raise _public_error(503, "artifact_authority_unavailable") from error
            except RuntimeError as error:
                raise _public_error(503, "artifact_authority_unavailable") from error
            except SQLAlchemyError as error:
                raise _public_error(503, "operation_authority_unavailable") from error

    async def _admit(
        self,
        session: AsyncSession,
        *,
        actor: PublicRequestActor,
        operation: WorkspaceOperation,
        operation_kind: str,
        request_plan: ProviderRequestPlan | None,
        resource_plan: WeightedResourcePlan,
    ) -> PublicOperationAdmission:
        return await self._admission(
            session,
            principal=PrincipalContext(
                owner_id=actor.owner_id,
                tenant_id=actor.tenant_id,
                trace_id=actor.correlation_id,
            ),
            operation_id=operation.id,
            operation_kind=operation_kind,
            request_plan=request_plan,
            resource_plan=resource_plan,
        )

    @staticmethod
    def _resource_plan(*, slide_count: int, fidelity_mode: str) -> WeightedResourcePlan:
        return plan_weighted_resources(
            pixel_count=max(1, slide_count) * 1_920 * 1_080,
            device_pixel_ratio_milli=1_000,
            dom_node_count=max(1, slide_count) * 500,
            font_count=4,
            svg_complexity=0,
            estimated_memory_mib=512,
            fidelity_mode=fidelity_mode,
        )

    async def _operation_for_key(
        self,
        session: AsyncSession,
        *,
        actor: PublicRequestActor,
        kind: str,
        key_hash: str,
        presentation_id: uuid.UUID | None = None,
    ) -> WorkspaceOperation | None:
        statement = select(WorkspaceOperation).where(
            WorkspaceOperation.owner_id == actor.owner_id,
            WorkspaceOperation.owner_scope == str(actor.owner_id),
            WorkspaceOperation.kind == kind,
            WorkspaceOperation.idempotency_key_hash == key_hash,
        )
        if presentation_id is not None:
            statement = statement.where(
                WorkspaceOperation.presentation_id == presentation_id
            )
        return await session.scalar(statement)

    async def _duplicate_receipt(
        self,
        session: AsyncSession,
        operation: WorkspaceOperation,
        *,
        actor: PublicRequestActor,
        request_hash: str,
    ) -> OperationReceiptV2:
        if operation.request_hash != request_hash:
            raise _public_error(409, "idempotency_key_conflict")
        admission = await session.scalar(
            select(PublicOperationAdmission).where(
                PublicOperationAdmission.operation_id == operation.id,
                PublicOperationAdmission.owner_id == actor.owner_id,
                PublicOperationAdmission.tenant_id == actor.tenant_id,
            )
        )
        if admission is None or not is_public_operation_authority(operation, admission):
            raise _public_error(503, "operation_authority_unavailable")
        return self._receipt(operation, admission, duplicate=True)

    async def _public_operation(
        self,
        session: AsyncSession,
        *,
        actor: PublicRequestActor,
        operation_id: str,
    ) -> tuple[WorkspaceOperation, PublicOperationAdmission]:
        operation_uuid = _operation_uuid(operation_id, reason="operation_not_found")
        operation = await session.scalar(
            select(WorkspaceOperation).where(
                WorkspaceOperation.id == operation_uuid,
                WorkspaceOperation.owner_id == actor.owner_id,
                WorkspaceOperation.owner_scope == str(actor.owner_id),
            )
        )
        if operation is None:
            raise _public_error(404, "operation_not_found")
        admission = await session.scalar(
            select(PublicOperationAdmission).where(
                PublicOperationAdmission.operation_id == operation.id,
                PublicOperationAdmission.owner_id == actor.owner_id,
                PublicOperationAdmission.tenant_id == actor.tenant_id,
            )
        )
        if admission is None or not is_public_operation_authority(operation, admission):
            raise _public_error(503, "operation_authority_unavailable")
        return operation, admission

    @staticmethod
    def _receipt(
        operation: WorkspaceOperation,
        admission: PublicOperationAdmission,
        *,
        duplicate: bool,
    ) -> OperationReceiptV2:
        operation_id = str(operation.id)
        base = f"/api/v2/operations/{operation_id}"
        return OperationReceiptV2(
            operation_id=operation_id,
            receipt_id=f"rcpt_{operation_id}",
            presentation_id=str(operation.presentation_id),
            status=_operation_status(operation.state),
            duplicate=duplicate,
            joined_existing_work=False,
            accepted_at=_aware(operation.created_at),
            absolute_deadline_at=_aware(admission.deadline_at),
            links=OperationLinksV2(
                self=base,
                events=f"{base}/events",
                cancel=f"{base}/cancel",
            ),
        )

    async def _artifacts(
        self,
        session: AsyncSession,
        actor: PublicRequestActor,
        operation: WorkspaceOperation,
    ) -> list[PublicArtifactV2]:
        if operation.state != "succeeded" or operation.result_revision is None:
            return []
        rows = list(
            (
                await session.scalars(
                    select(PublicArtifact)
                    .join(
                        PublicArtifactPublication,
                        PublicArtifactPublication.artifact_id == PublicArtifact.id,
                    )
                    .where(
                        PublicArtifact.owner_id == actor.owner_id,
                        PublicArtifact.tenant_id == actor.tenant_id,
                        PublicArtifact.created_by_operation_id == operation.id,
                        PublicArtifact.lifecycle_state == "committed",
                        PublicArtifactPublication.operation_id == operation.id,
                        PublicArtifactPublication.revision == operation.result_revision,
                        PublicArtifactPublication.fencing_token
                        == operation.fencing_token,
                        PublicArtifact.kind.in_(
                            ("presentation-pptx", "presentation-pdf")
                        ),
                    )
                    .order_by(PublicArtifact.created_at)
                )
            ).all()
        )
        return [
            PublicArtifactV2(
                artifact_id=str(row.id),
                operation_id=str(operation.id),
                presentation_id=str(operation.presentation_id),
                kind=row.kind,
                digest=f"sha256:{row.sha256}",
                size_bytes=row.size_bytes,
                media_type=row.media_type,
                download=f"/api/v2/artifacts/{row.id}/download",
            )
            for row in rows
        ]

    @staticmethod
    def _status(
        operation: WorkspaceOperation,
        admission: PublicOperationAdmission,
        artifacts: list[PublicArtifactV2],
    ) -> OperationStatusV2:
        public_status = _operation_status(operation.state)
        reason_code = operation.error_code
        if operation.state == "deadline_exceeded":
            reason_code = "deadline_exceeded"
        elif operation.state == "cancelled":
            reason_code = "cancelled_by_user"
        error = (
            PublicOperationErrorV2(
                reason_code=reason_code or "operation_failed",
                retryable=bool(
                    isinstance(operation.error_detail, Mapping)
                    and operation.error_detail.get("retryable") is True
                ),
            )
            if public_status in {"failed", "cancelled"}
            else None
        )
        quality: QualityReceiptV3 | None = None
        export_timing: PublicExportExecutionReceiptV1 | None = None
        generation_route_receipt: GenerationRouteReceiptV1 | None = None
        generation_timing_receipt: dict[str, object] | None = None
        content_integrity_receipt: dict[str, object] | None = None
        if operation.kind == _GENERATION_KIND and isinstance(
            operation.request_payload, Mapping
        ):
            raw_generation_route = operation.request_payload.get(
                "generation_route_receipt"
            )
            if isinstance(raw_generation_route, Mapping):
                try:
                    generation_route_receipt = (
                        GenerationRouteReceiptV1.model_validate(raw_generation_route)
                    )
                except ValueError:
                    generation_route_receipt = None
        if isinstance(operation.result, Mapping):
            if operation.kind == _GENERATION_KIND:
                from services.workspace_generation_timing_payload import (
                    sanitize_generation_timing_receipt,
                )

                generation_timing_receipt = (
                    sanitize_generation_timing_receipt(
                        operation.result.get("generation_timing_receipt")
                    )
                    or None
                )
                raw_content_integrity = operation.result.get(
                    "contentIntegrityReceipt"
                )
                if isinstance(raw_content_integrity, Mapping):
                    try:
                        from services.authored_vnext.narrative_ir.content_graph import (
                            validate_content_integrity_receipt_binding,
                        )

                        if (
                            generation_route_receipt is None
                            or generation_route_receipt.selected_route != "source-native"
                            or operation.expected_revision is None
                            or operation.result_revision is None
                        ):
                            raise ValueError("content_integrity_binding_unavailable")
                        authority = _checkpoint_content_graph_authority(operation)
                        validate_content_integrity_receipt_binding(
                            raw_content_integrity,
                            operation_id=operation.id,
                            source_revision=int(operation.expected_revision),
                            result_revision=int(operation.result_revision),
                            generation_lane=str(authority["generationLane"]),
                            content_graph_digest=str(authority["contentGraphDigest"]),
                            route_strategy_receipt_digest=(
                                generation_route_receipt.receipt_digest
                            ),
                            attempt_binding_digest=str(
                                authority["attemptBindingDigest"]
                            ),
                            narrative_digest=authority["narrativeDigest"],
                            preserve_source_input_digest=authority[
                                "preserveSourceInputDigest"
                            ],
                            provider_call_budget_digest=str(
                                authority["providerCallBudgetDigest"]
                            ),
                        )
                        content_integrity_receipt = dict(raw_content_integrity)
                    except Exception:
                        content_integrity_receipt = None
            raw_quality = operation.result.get("quality_receipt") or operation.result.get(
                "qualityReceipt"
            )
            if isinstance(raw_quality, Mapping):
                try:
                    candidate = QualityReceiptV3.model_validate(raw_quality)
                    if any(item.digest == candidate.artifact_digest for item in artifacts):
                        quality = candidate
                except ValueError:
                    quality = None
            raw_export_timing = operation.result.get(
                "public_export_execution_receipt"
            )
            if isinstance(raw_export_timing, Mapping):
                try:
                    export_timing = PublicExportExecutionReceiptV1.model_validate(
                        raw_export_timing
                    )
                except ValueError:
                    export_timing = None
        readiness = quality.release_readiness if quality is not None else "not_assessed"
        fidelity_artifact_ready = bool(readiness == "ready" and artifacts)
        phase = _phase(
            kind=operation.kind,
            state=operation.state,
            progress=operation.progress,
        )
        progress = operation.progress if isinstance(operation.progress, Mapping) else {}
        raw_queue_position = progress.get("queue_position")
        queue_position = (
            raw_queue_position
            if isinstance(raw_queue_position, int)
            and not isinstance(raw_queue_position, bool)
            and raw_queue_position >= 1
            else None
        )
        if operation.kind == _GENERATION_KIND:
            export_state = "not_requested"
        elif public_status == "succeeded":
            export_state = "completed"
        elif public_status in {"failed", "cancelled"}:
            export_state = "failed"
        elif public_status == "running":
            export_state = "running"
        else:
            export_state = "pending"
        publication_state = (
            "published"
            if artifacts
            else (
                "unavailable"
                if public_status in {"failed", "cancelled"}
                else "pending"
            )
        )
        execution_receipt = OperationExecutionReceiptV2(
            attempt_number=max(0, int(operation.attempt_number)),
            retry_count=max(0, int(operation.retry_count)),
            max_attempts=max(1, int(operation.max_attempts)),
            queue_position=queue_position,
            stage=phase,
            publication=publication_state,
            export=export_state,
            timing=export_timing,
        )
        return OperationStatusV2(
            operation_id=str(operation.id),
            presentation_id=str(operation.presentation_id),
            kind=(
                "presentation.generate"
                if operation.kind == _GENERATION_KIND
                else "presentation.export"
            ),
            status=public_status,
            phase=phase,
            progress_percent=_progress_percent(operation.progress),
            source_revision=(
                int(operation.result_revision)
                if operation.state == "succeeded"
                and operation.result_revision is not None
                else None
            ),
            accepted_at=_aware(operation.created_at),
            updated_at=_aware(operation.updated_at),
            absolute_deadline_at=_aware(admission.deadline_at),
            error=error,
            generation_route_receipt=generation_route_receipt,
            generation_timing_receipt=generation_timing_receipt,
            content_integrity_receipt=content_integrity_receipt,
            execution_receipt=execution_receipt,
            quality_receipt=quality,
            release_readiness=readiness,
            readiness_scope="artifact:fidelity",
            fidelity_artifact_ready=fidelity_artifact_ready,
            native_editability_ready=False,
            promotion_ready=False,
            deployment_authorized=False,
            artifacts=artifacts,
        )

    @staticmethod
    def _event(
        operation: WorkspaceOperation,
        event: WorkspaceOperationEvent,
    ) -> OperationEventV2:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        raw_state = str(payload.get("state") or payload.get("status") or operation.state)
        raw_percent = payload.get("percent")
        if raw_percent is None:
            raw_percent = payload.get("progress")
        progress = {
            "stage": payload.get("stage") or payload.get("phase"),
            "percent": raw_percent,
        }
        reason_code = payload.get("reason_code") or payload.get("error_code")
        return OperationEventV2(
            event_id=f"evt_{event.cursor or event.sequence}",
            operation_id=str(operation.id),
            sequence=event.sequence,
            occurred_at=_aware(event.created_at),
            status=_operation_status(raw_state),
            phase=_phase(kind=operation.kind, state=raw_state, progress=progress),
            progress_percent=_progress_percent(progress),
            reason_code=str(reason_code) if reason_code else None,
        )


def create_public_v2_orchestrator(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    integration_profile: str,
) -> SqlPublicV2Orchestrator:
    """Build the app-scoped adapter without touching the database at import time."""

    if integration_profile != PUBLIC_V2_POSTGRESQL_STRICT_PROFILE:
        raise RuntimeError("public_v2.integration_profile_invalid")
    return SqlPublicV2Orchestrator(session_factory)


__all__ = [
    "PUBLIC_V2_INTEGRATION_PROFILE_ENV",
    "PUBLIC_V2_POSTGRESQL_STRICT_PROFILE",
    "SqlPublicV2Orchestrator",
    "create_public_v2_orchestrator",
    "resolve_public_v2_integration_profile",
]
