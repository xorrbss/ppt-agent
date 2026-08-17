"""Target-owned workspace operation for an AI-authored template change."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import uuid

from services.presentation_generation_operation_handler import (
    PresentationGenerationOperationHandler,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)


TEMPLATE_APPLY_OPERATION_KIND = "template.apply"
DESIGN_APPLY_OPERATION_KIND = "design.apply"


def _generation_settings(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("generation_settings")
    return value if isinstance(value, Mapping) else {}


class WorkspaceAuthoredTemplateOperationHandler:
    """Run a resolved design application while keeping ownership on target."""

    def __init__(
        self,
        *,
        generation_handler: PresentationGenerationOperationHandler | None = None,
    ) -> None:
        self._generation_handler = (
            generation_handler or PresentationGenerationOperationHandler()
        )

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )
        settings = _generation_settings(claim.request_payload)
        raw_source_id = settings.get("source_presentation_id")
        raw_source_revision = settings.get("source_revision")
        raw_style = settings.get("authored_style")
        authored_style = raw_style.strip() if isinstance(raw_style, str) else ""
        vision_qa = settings.get("vision_qa", False)
        style_intent = settings.get("authored_style_intent")
        master_intent = settings.get("master_intent")
        resolved_master_pin = settings.get("resolved_master_pin")
        resolved_composition_pin = settings.get("resolved_composition_pin")
        resolved_style_digest = settings.get("resolved_style_digest")
        resolved_rule_snapshot = settings.get("resolved_rule_snapshot")
        resolved_rule_digest = settings.get("resolved_rule_digest")
        design_signature = settings.get("design_signature")
        try:
            source_presentation_id = uuid.UUID(str(raw_source_id))
            source_revision = int(raw_source_revision)
        except (TypeError, ValueError) as error:
            raise NonRetryableWorkspaceOperationError(
                "authored_template_request_invalid"
            ) from error
        if (
            source_presentation_id == claim.presentation_id
            or source_revision < 0
            or not authored_style
            or not isinstance(vision_qa, bool)
        ):
            raise NonRetryableWorkspaceOperationError(
                "authored_template_request_invalid"
            )
        if claim.kind == DESIGN_APPLY_OPERATION_KIND:
            if (
                not isinstance(style_intent, Mapping)
                or style_intent.get("intent") not in {"keep", "set"}
                or not isinstance(master_intent, Mapping)
                or master_intent.get("intent") not in {"keep", "set", "clear"}
                or not isinstance(design_signature, str)
                or not design_signature.startswith("sha256:")
                or not isinstance(resolved_style_digest, str)
                or not resolved_style_digest.startswith("sha256:")
                or (resolved_master_pin is None) != (resolved_composition_pin is None)
                or (resolved_master_pin is None) != (resolved_rule_snapshot is None)
                or (resolved_master_pin is None) != (resolved_rule_digest is None)
                or (
                    resolved_rule_digest is not None
                    and (
                        not isinstance(resolved_rule_digest, str)
                        or not resolved_rule_digest.startswith("sha256:")
                    )
                )
            ):
                raise NonRetryableWorkspaceOperationError(
                    "authored_template_request_invalid"
                )

        generated = await self._generation_handler.execute(context)
        resolved_result = (
            {
                "resolved_style_digest": resolved_style_digest,
                "resolved_master_pin": resolved_master_pin,
                "resolved_composition_pin": resolved_composition_pin,
                "resolved_rule_snapshot": resolved_rule_snapshot,
                "resolved_rule_digest": resolved_rule_digest,
            }
            if claim.kind == DESIGN_APPLY_OPERATION_KIND
            else {}
        )
        return WorkspaceOperationResult(
            payload={
                **generated.payload,
                "kind": claim.kind,
                "source_presentation_id": str(source_presentation_id),
                "source_revision": source_revision,
                "target_presentation_id": str(claim.presentation_id),
                "presentation_id": str(claim.presentation_id),
                "authored_style": authored_style,
                **resolved_result,
            },
            result_revision=generated.result_revision,
        )


def register_workspace_authored_template_operation_handler(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> WorkspaceAuthoredTemplateOperationHandler:
    handler = WorkspaceAuthoredTemplateOperationHandler()
    registry.register(
        DESIGN_APPLY_OPERATION_KIND,
        handler,
        replace=replace,
        recovery_policy="automatic",
    )
    registry.register(
        TEMPLATE_APPLY_OPERATION_KIND,
        handler,
        replace=replace,
        recovery_policy="automatic",
    )
    return handler
