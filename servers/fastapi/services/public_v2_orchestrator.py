"""PostgreSQL-backed adapter for the canonical public v2 API.

The adapter only admits durable work and projects durable state. Provider,
render, and export execution remains owned by the workspace worker boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Iterator
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v2.contracts import (
    AuthorizedDownloadGrant,
    CancelOperationReceiptV2,
    OperationEventPageV2,
    OperationEventV2,
    OperationLinksV2,
    OperationReceiptV2,
    OperationStatusV2,
    PresentationExportRequestV2,
    PresentationGenerationRequestV2,
    PublicArtifactV2,
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

PUBLIC_V2_POSTGRESQL_STRICT_PROFILE = "postgresql-strict"
PUBLIC_V2_INTEGRATION_PROFILE_ENV = "PUBLIC_V2_INTEGRATION_PROFILE"
_PUBLIC_CONTRACT = "public-operation/v2"
_PUBLIC_PROFILE = "public-generation-export/v2"
_GENERATION_KIND = "presentation.generate"
_TERMINAL_STATES = {
    "succeeded",
    "failed",
    "cancelled",
    "deadline_exceeded",
    "dead_letter",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


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


def _operation_status(value: str) -> str:
    if value in {"queued", "waiting_for_assets", "awaiting_approval", "approval_required"}:
        return "queued"
    if value in {"running", "cancelling", "blocked"}:
        return "running"
    if value == "succeeded":
        return "succeeded"
    if value == "cancelled":
        return "cancelled"
    return "failed"


def _phase(*, kind: str, state: str, progress: object) -> str | None:
    stage = ""
    if isinstance(progress, Mapping):
        stage = str(progress.get("stage") or progress.get("phase") or "").lower()
    if "publish" in stage:
        return "publishing"
    if "package" in stage or "export_completed" in stage:
        return "packaging"
    if "render" in stage or "export" in stage:
        return "rendering"
    if "valid" in stage or "quality" in stage:
        return "validating"
    if "author" in stage or "outline" in stage or "generation" in stage:
        return "authoring"
    if state == "queued":
        return "admission"
    if kind.startswith("export.") and state in {"running", "cancelling"}:
        return "rendering"
    if kind == _GENERATION_KIND and state in {"running", "cancelling"}:
        return "authoring"
    return None


def _progress_percent(progress: object) -> int | None:
    if not isinstance(progress, Mapping):
        return None
    raw = progress.get("percent")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return max(0, min(100, int(raw)))


class SqlPublicV2Orchestrator:
    """Thin durable admission/projection adapter for public v2 routes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        admission: AdmissionCallable = admit_public_operation_v2,
        cancel: CancelCallable = request_workspace_operation_cancellation,
        artifact_service_factory: ArtifactServiceFactory = configured_public_artifact_service,
    ) -> None:
        self._session_factory = session_factory
        self._admission = admission
        self._cancel = cancel
        self._artifact_service_factory = artifact_service_factory

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
        request_payload = request.model_dump(mode="json", by_alias=True)
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
            )
            generation_payload = _durable_request_payload(internal_request)
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
        request_hash = _canonical_hash(request.model_dump(mode="json", by_alias=True))
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
            reservation = await reserve_conversation_positions(
                session,
                conversation.id,
                1,
            )
            operation = WorkspaceOperation(
                owner_id=actor.owner_id,
                owner_scope=str(actor.owner_id),
                conversation_id=conversation.id,
                presentation_id=target_id,
                anchor_position=reservation.start,
                kind=kind,
                state="queued",
                idempotency_key_hash=key_hash,
                request_hash=request_hash,
                request_payload={
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
                },
                expected_revision=request.source_revision,
                next_attempt_at=get_current_utc_datetime(),
            )
            session.add(operation)
            await session.flush()
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
                    next_cursor = (
                        str(rows[-1].cursor)
                        if len(rows) == limit and rows[-1].cursor is not None
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
        if admission is None or not self._is_public_authority(operation, admission):
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
        if admission is None or not self._is_public_authority(operation, admission):
            raise _public_error(503, "operation_authority_unavailable")
        return operation, admission

    @staticmethod
    def _is_public_authority(
        operation: WorkspaceOperation,
        admission: PublicOperationAdmission,
    ) -> bool:
        return (
            operation.contract_version == _PUBLIC_CONTRACT
            and operation.execution_profile_version == _PUBLIC_PROFILE
            and operation.deadline_at is not None
            and operation.fencing_token == admission.fence
            and _aware(operation.deadline_at) == _aware(admission.deadline_at)
        )

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
        if isinstance(operation.result, Mapping):
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
        readiness = quality.release_readiness if quality is not None else "not_assessed"
        return OperationStatusV2(
            operation_id=str(operation.id),
            presentation_id=str(operation.presentation_id),
            kind=(
                "presentation.generate"
                if operation.kind == _GENERATION_KIND
                else "presentation.export"
            ),
            status=public_status,
            phase=_phase(
                kind=operation.kind,
                state=operation.state,
                progress=operation.progress,
            ),
            progress_percent=_progress_percent(operation.progress),
            accepted_at=_aware(operation.created_at),
            updated_at=_aware(operation.updated_at),
            absolute_deadline_at=_aware(admission.deadline_at),
            error=error,
            quality_receipt=quality,
            release_readiness=readiness,
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
