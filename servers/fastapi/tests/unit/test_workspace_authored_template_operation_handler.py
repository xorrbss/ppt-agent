import asyncio
import uuid

import pytest

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from services.workspace_authored_template_operation_handler import (
    WorkspaceAuthoredTemplateOperationHandler,
    _safe_generation_failure,
    register_workspace_authored_template_operation_handler,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
)


class _Context:
    def __init__(self, *, expected_revision: int | None, context: dict | None = None):
        self.claim = WorkspaceOperationClaim(
            operation_id=uuid.uuid4(),
            owner_id=None,
            owner_scope="anonymous:test",
            conversation_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            kind="template.apply",
            request_payload={"context": context or {}},
            expected_revision=expected_revision,
            attempt_number=1,
            attempt_token="attempt-token",
            fencing_token=1,
        )

    async def progress(self, **_kwargs):
        return True

    async def cancellation_requested(self):
        return False

    async def raise_if_cancelled(self):
        return None


def test_template_apply_registration_is_automatic_and_resolvable():
    registry = WorkspaceOperationHandlerRegistry()

    handler = register_workspace_authored_template_operation_handler(
        registry=registry
    )

    assert registry.resolve("template.apply") is handler
    assert registry.recovery_policy("template.apply") == "automatic"


@pytest.mark.parametrize(
    ("context", "expected_code"),
    [
        ({"authored_style": "", "vision_qa": False}, "authored_template_request_invalid"),
        ({"authored_style": "style-7", "vision_qa": "yes"}, "authored_template_request_invalid"),
    ],
)
def test_template_apply_rejects_invalid_command_context_before_database_access(
    context, expected_code
):
    handler = WorkspaceAuthoredTemplateOperationHandler()

    with pytest.raises(NonRetryableWorkspaceOperationError) as captured:
        asyncio.run(handler.execute(_Context(expected_revision=3, context=context)))

    assert captured.value.code == expected_code


def test_template_apply_requires_a_revision_fence():
    handler = WorkspaceAuthoredTemplateOperationHandler()

    with pytest.raises(NonRetryableWorkspaceOperationError) as captured:
        asyncio.run(
            handler.execute(
                _Context(
                    expected_revision=None,
                    context={"authored_style": "style-7", "vision_qa": False},
                )
            )
        )

    assert captured.value.code == "workspace_expected_revision_required"


def test_generation_failure_projection_preserves_retryability_without_leaking_detail():
    task = AsyncPresentationGenerationTaskModel(
        status="error",
        error={
            "detail": {"code": "provider_timeout", "retryable": True},
            "private_prompt": "must-not-escape",
        },
    )

    assert _safe_generation_failure(task) == ("provider_timeout", True)
