"""Atomic confirmation admission for AI-authored template applications."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.generate_presentation_request import AuthoredMasterPinInput
from models.presentation_from_template import RetemplatePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    get_workspace_owner_scope,
)
from models.workspace import (
    WorkspaceBootstrapRequest,
    WorkspaceTemplateApplicationRequest,
)
from services.presentation_generation_job_service import (
    idempotency_key_hash,
    request_sha256,
)
from services.workspace_service import (
    WorkspaceServiceError,
    bootstrap_workspace,
    canonical_payload_hash,
)
from services.authored_vnext.generation.effective_style import (
    resolve_authored_composition,
)
from services.authored_vnext.generation.legacy_pin import (
    canonical_legacy_master_pin_identity,
    resolve_legacy_master_pin,
)
from utils.owner_context import get_current_owner_id
from utils.authored_styles import resolve_authored_style


GenerationRequestBuilder = Callable[
    [uuid.UUID, RetemplatePresentationRequest, AsyncSession],
    Awaitable[GeneratePresentationRequest],
]


@dataclass(frozen=True, slots=True)
class ConfirmedTemplateApplication:
    source_presentation_id: uuid.UUID
    source_revision: int
    target_presentation_id: uuid.UUID
    conversation: WorkspaceConversation
    message: WorkspaceMessage
    operation: WorkspaceOperation
    duplicate: bool


def _confirmation_payload(
    source_presentation_id: uuid.UUID,
    request: WorkspaceTemplateApplicationRequest,
) -> dict:
    return {
        "source_presentation_id": str(source_presentation_id),
        **request.model_dump(mode="json"),
    }


def _source_authored_projection(source: PresentationModel) -> dict:
    theme = source.theme if isinstance(source.theme, dict) else {}
    projection = theme.get("authored_vnext")
    return projection if isinstance(projection, dict) else {}


def _resolve_style_id(
    source: PresentationModel,
    request: WorkspaceTemplateApplicationRequest,
) -> str:
    selection = request.authored_style
    if selection.intent == "set":
        assert selection.style_id is not None
        return selection.style_id
    projection = _source_authored_projection(source)
    composition = projection.get("compositionPin")
    if isinstance(composition, dict):
        style_id = composition.get("styleId")
        if isinstance(style_id, str) and style_id.strip():
            return style_id.strip()
    theme = source.theme if isinstance(source.theme, dict) else {}
    style_id = theme.get("style")
    if not isinstance(style_id, str) or not style_id.strip():
        raise WorkspaceServiceError(
            "authored_template_request_invalid", status_code=400
        )
    return style_id.strip()


def _source_master_pin(source: PresentationModel) -> AuthoredMasterPinInput | None:
    projection = _source_authored_projection(source)
    raw_pin = projection.get("masterPin")
    if raw_pin is None:
        return None
    identity = canonical_legacy_master_pin_identity(raw_pin)
    if identity is None:
        raise WorkspaceServiceError(
            "authored_template_request_invalid", status_code=400
        )
    master_id, revision, digest, _, _ = identity
    return AuthoredMasterPinInput(
        master_id=master_id,
        revision=revision,
        digest=digest,
    )


def _resolve_master_pin(
    source: PresentationModel,
    request: WorkspaceTemplateApplicationRequest,
) -> AuthoredMasterPinInput | None:
    selection = request.master
    if selection.intent == "clear":
        return None
    if selection.intent == "set":
        assert selection.pin is not None
        return selection.pin
    return _source_master_pin(source)


async def _existing_confirmation(
    session: AsyncSession,
    *,
    key_hash: str,
    confirmation_hashes: frozenset[str],
    design_signature: str,
) -> ConfirmedTemplateApplication | None:
    row = (
        await session.execute(
            select(
                PresentationGenerationJob,
                WorkspaceOperation,
                WorkspaceConversation,
            )
            .join(
                WorkspaceOperation,
                WorkspaceOperation.id
                == PresentationGenerationJob.workspace_operation_id,
            )
            .join(
                WorkspaceConversation,
                WorkspaceConversation.id == WorkspaceOperation.conversation_id,
            )
            .where(PresentationGenerationJob.idempotency_key_hash == key_hash)
        )
    ).one_or_none()
    if row is None:
        return None
    job, operation, conversation = row
    if (
        operation.owner_scope != get_workspace_owner_scope()
        or operation.kind not in {"design.apply", "template.apply"}
    ):
        raise WorkspaceServiceError("presentation_not_found", status_code=404)
    payload = (
        operation.request_payload
        if isinstance(operation.request_payload, dict)
        else {}
    )
    if payload.get("confirmation_request_hash") not in confirmation_hashes:
        raise WorkspaceServiceError(
            "idempotency_key_payload_conflict", status_code=409
        )
    stored_design_signature = payload.get("design_signature")
    if (
        operation.kind == "design.apply"
        and stored_design_signature != design_signature
    ):
        raise WorkspaceServiceError(
            "idempotency_key_payload_conflict", status_code=409
        )
    source_id = payload.get("source_presentation_id")
    source_revision = payload.get("source_revision")
    try:
        parsed_source_id = uuid.UUID(str(source_id))
        parsed_source_revision = int(source_revision)
        message_id = uuid.UUID(str(payload.get("user_message_id")))
    except (TypeError, ValueError) as error:
        raise WorkspaceServiceError(
            "idempotency_record_incomplete", status_code=409
        ) from error
    message = await session.get(WorkspaceMessage, message_id)
    if message is None or message.conversation_id != conversation.id:
        raise WorkspaceServiceError(
            "idempotency_record_incomplete", status_code=409
        )
    return ConfirmedTemplateApplication(
        source_presentation_id=parsed_source_id,
        source_revision=parsed_source_revision,
        target_presentation_id=job.presentation_id,
        conversation=conversation,
        message=message,
        operation=operation,
        duplicate=True,
    )


async def confirm_authored_template_application(
    session: AsyncSession,
    *,
    source_presentation_id: uuid.UUID,
    request: WorkspaceTemplateApplicationRequest,
    idempotency_key: str,
    target_presentation_id: uuid.UUID | None = None,
    generation_request_builder: GenerationRequestBuilder | None = None,
) -> ConfirmedTemplateApplication:
    """Create or reuse the complete target-owned application graph."""

    namespaced_key = (
        "workspace-template-application:"
        f"{source_presentation_id}:{idempotency_key}"
    )
    try:
        key_hash = idempotency_key_hash(namespaced_key)
    except ValueError as error:
        raise WorkspaceServiceError(str(error), status_code=400) from error
    owner_id = get_current_owner_id()
    owner_predicate = (
        PresentationModel.owner_id.is_(None)
        if owner_id is None
        else PresentationModel.owner_id == owner_id
    )
    locked_revision = (
        await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == source_presentation_id,
                owner_predicate,
                PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
            )
            .values(updated_at=PresentationModel.updated_at)
            .returning(PresentationModel.revision)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if locked_revision is None:
        raise WorkspaceServiceError("presentation_not_found", status_code=404)
    if int(locked_revision) != request.source_revision:
        raise WorkspaceServiceError(
            "revision_conflict", status_code=409, retryable=True
        )
    source = await session.get(PresentationModel, source_presentation_id)
    if source is None:
        raise WorkspaceServiceError("presentation_not_found", status_code=404)

    resolved_style_id = _resolve_style_id(source, request)
    resolved_master_input = _resolve_master_pin(source, request)

    if generation_request_builder is None:
        from api.v1.ppt.endpoints.authored_operations import (
            build_authored_retemplate_generation_request,
        )

        generation_request_builder = build_authored_retemplate_generation_request
    generation_request = await generation_request_builder(
        source_presentation_id,
        RetemplatePresentationRequest(
            authored_style=resolved_style_id,
            master_pin=resolved_master_input,
            vision_qa=request.vision_qa,
        ),
        session,
    )

    # ``keep`` is a compatibility fork of the exact company-standard pin on
    # the source presentation.  A newer published head must not invalidate
    # that immutable checkpoint.  Explicit ``set`` selections still take the
    # strict current-head admission path in check_if_api_request_is_valid.
    if request.master.intent == "keep" and generation_request.master_pin is not None:
        owner_id = get_current_owner_id()
        if owner_id is not None:
            kept_master = await asyncio.to_thread(
                resolve_legacy_master_pin,
                owner_id=owner_id,
                requested=generation_request.master_pin,
                require_current_head=False,
            )
            kept_style = resolve_authored_style(resolved_style_id)
            kept_composition, _ = resolve_authored_composition(
                kept_master.contract,
                style=kept_style,
                metadata=generation_request._authored_deck_metadata,
            )
            generation_request._authored_master_pin = kept_master.pin
            generation_request._authored_master_contract = kept_master.contract
            generation_request._authored_master_resolution = kept_master
            generation_request._authored_style_snapshot = kept_style
            generation_request._authored_composition_pin = kept_composition

    from api.v1.ppt.endpoints.presentation_generate import (
        _durable_request_payload,
        check_if_api_request_is_valid,
    )

    await check_if_api_request_is_valid(generation_request, session)
    generation_request._authored_design_replay = True
    resolved_style = resolve_authored_style(resolved_style_id)
    generation_request._authored_style_snapshot = resolved_style
    resolved_master_pin = generation_request._authored_master_pin
    resolved_composition_pin = None
    if generation_request._authored_master_resolution is not None:
        resolved_composition_pin, _ = resolve_authored_composition(
            generation_request._authored_master_resolution.contract,
            style=resolved_style,
            metadata=generation_request._authored_deck_metadata,
        )
        generation_request._authored_composition_pin = resolved_composition_pin

    resolved_master_payload = (
        resolved_master_pin.model_dump(mode="json", by_alias=True)
        if resolved_master_pin is not None
        else None
    )
    resolved_composition_payload = (
        resolved_composition_pin.model_dump(mode="json", by_alias=True)
        if resolved_composition_pin is not None
        else None
    )
    resolved_style_digest = "sha256:" + canonical_payload_hash(
        {
            "id": resolved_style.id,
            "brief": resolved_style.brief,
            "previewAccent": resolved_style.preview_accent,
            "previewBackground": resolved_style.preview_bg,
            "previewPalette": list(resolved_style.preview_palette or ()),
            "previewVariant": resolved_style.preview_variant,
            "primaryColor": resolved_style.primary_color,
            "backgroundColor": resolved_style.background_color,
            "headingFont": resolved_style.heading_font,
            "bodyFont": resolved_style.body_font,
            "monoFont": resolved_style.mono_font,
            "illustrationPrompt": resolved_style.illustration_prompt,
        }
    )
    resolved_design = {
        "authoredStyle": {
            "intent": request.authored_style.intent,
            **(
                {"styleId": request.authored_style.style_id}
                if request.authored_style.style_id is not None
                else {}
            ),
        },
        "master": request.master.model_dump(mode="json", by_alias=True),
        "resolvedStyleId": resolved_style_id,
        "resolvedStyleDigest": resolved_style_digest,
        "resolvedMasterPin": resolved_master_payload,
        "resolvedCompositionPin": resolved_composition_payload,
        "resolvedRuleSnapshot": (
            resolved_composition_pin.consistency_policy.model_dump(
                mode="json", by_alias=True
            )
            if resolved_composition_pin is not None
            and resolved_composition_pin.consistency_policy is not None
            else None
        ),
        "resolvedRuleDigest": (
            resolved_composition_pin.consistency_policy.digest
            if resolved_composition_pin is not None
            and resolved_composition_pin.consistency_policy is not None
            else None
        ),
        "visionQa": request.vision_qa,
    }
    design_signature = "sha256:" + canonical_payload_hash(resolved_design)
    confirmation_request_hash = canonical_payload_hash(
        {
            **_confirmation_payload(source_presentation_id, request),
            "resolved_design": resolved_design,
        }
    )
    accepted_confirmation_hashes = {confirmation_request_hash}
    if (
        request.authored_style.intent == "set"
        and request.authored_style.style_id is not None
        and request.master.intent == "keep"
    ):
        accepted_confirmation_hashes.add(
            canonical_payload_hash(
                {
                    "source_presentation_id": str(source_presentation_id),
                    "source_revision": request.source_revision,
                    "prompt": request.prompt,
                    "authored_style": request.authored_style.style_id,
                    "vision_qa": request.vision_qa,
                }
            )
        )
    existing = await _existing_confirmation(
        session,
        key_hash=key_hash,
        confirmation_hashes=frozenset(accepted_confirmation_hashes),
        design_signature=design_signature,
    )
    if existing is not None:
        return existing

    target = source.get_new_presentation()
    if target_presentation_id is not None:
        if target_presentation_id == source_presentation_id:
            raise WorkspaceServiceError(
                "bootstrap_concurrency_conflict", status_code=409
            )
        target.id = target_presentation_id
    target.lifecycle_status = PRESENTATION_LIFECYCLE_STAGING
    target.revision = 0
    target.share_token = None
    target.deleted_at = None
    target.deletion_operation_id = None
    generation_request.presentation_id = target.id
    durable_generation_payload = _durable_request_payload(generation_request)

    bootstrap_request = WorkspaceBootstrapRequest(
        idempotency_key=idempotency_key,
        prompt=request.prompt,
        generation_settings={
            "source_presentation_id": str(source_presentation_id),
            "source_revision": request.source_revision,
            "authored_style": resolved_style_id,
            "authored_style_intent": request.authored_style.model_dump(
                mode="json", by_alias=True
            ),
            "master_intent": request.master.model_dump(
                mode="json", by_alias=True
            ),
            "resolved_master_pin": resolved_master_payload,
            "resolved_composition_pin": resolved_composition_payload,
            "resolved_rule_snapshot": resolved_design["resolvedRuleSnapshot"],
            "resolved_rule_digest": resolved_design["resolvedRuleDigest"],
            "resolved_style_digest": resolved_style_digest,
            "design_signature": design_signature,
            "vision_qa": request.vision_qa,
        },
        operation_type="design.apply",
        expected_revision=0,
    )
    session.add(target)
    try:
        await session.flush()
        conversation, message, operation, _ = await bootstrap_workspace(
            session,
            presentation_id=target.id,
            request=bootstrap_request,
            commit=False,
            initial_operation_state="queued",
        )
        task = AsyncPresentationGenerationTaskModel(
            status="pending",
            message="Queued for generation",
            data=None,
        )
        job = PresentationGenerationJob(
            task_id=task.id,
            presentation_id=target.id,
            workspace_operation_id=operation.id,
            idempotency_key_hash=key_hash,
            request_sha256=request_sha256(durable_generation_payload),
            request_payload=durable_generation_payload,
            export_cookie_required=False,
            state="queued",
        )
        operation.request_payload = {
            **operation.request_payload,
            "confirmation_request_hash": confirmation_request_hash,
            "design_signature": design_signature,
            "resolved_design": resolved_design,
            "source_presentation_id": str(source_presentation_id),
            "source_revision": request.source_revision,
            "target_presentation_id": str(target.id),
            "generation_request": durable_generation_payload,
            "task_id": task.id,
            "job_id": str(job.id),
        }
        session.add_all((task, job, operation))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _existing_confirmation(
            session,
            key_hash=key_hash,
            confirmation_hashes=frozenset(accepted_confirmation_hashes),
            design_signature=design_signature,
        )
        if existing is None:
            raise WorkspaceServiceError(
                "bootstrap_concurrency_conflict", status_code=409
            )
        return existing

    return ConfirmedTemplateApplication(
        source_presentation_id=source_presentation_id,
        source_revision=request.source_revision,
        target_presentation_id=target.id,
        conversation=conversation,
        message=message,
        operation=operation,
        duplicate=False,
    )
