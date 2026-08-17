import asyncio
from dataclasses import replace
import uuid

import pytest

from services.workspace_authored_template_operation_handler import (
    DESIGN_APPLY_OPERATION_KIND,
    WorkspaceAuthoredTemplateOperationHandler,
    register_workspace_authored_template_operation_handler,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)


class _Context:
    def __init__(
        self,
        *,
        expected_revision: int | None,
        settings: dict | None = None,
        kind: str = "template.apply",
    ):
        self.claim = WorkspaceOperationClaim(
            operation_id=uuid.uuid4(),
            owner_id=None,
            owner_scope="anonymous:test",
            conversation_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            kind=kind,
            request_payload={"generation_settings": settings or {}},
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


class _GenerationHandler:
    def __init__(self):
        self.context = None

    async def execute(self, context):
        self.context = context
        return WorkspaceOperationResult(
            payload={"status": "completed"},
            result_revision=1,
        )


def _settings(source_id: uuid.UUID | None = None) -> dict:
    return {
        "source_presentation_id": str(source_id or uuid.uuid4()),
        "source_revision": 7,
        "authored_style": "signal",
        "vision_qa": False,
    }


def _design_settings(source_id: uuid.UUID | None = None) -> dict:
    return _settings(source_id) | {
        "authored_style_intent": {"intent": "keep"},
        "master_intent": {"intent": "clear"},
        "resolved_master_pin": None,
        "resolved_composition_pin": None,
        "resolved_style_digest": "sha256:" + "b" * 64,
        "design_signature": "sha256:" + "a" * 64,
    }


def test_template_apply_registration_is_automatic_and_resolvable():
    registry = WorkspaceOperationHandlerRegistry()

    handler = register_workspace_authored_template_operation_handler(
        registry=registry
    )

    assert registry.resolve("template.apply") is handler
    assert registry.resolve(DESIGN_APPLY_OPERATION_KIND) is handler
    assert registry.recovery_policy("template.apply") == "automatic"
    assert registry.recovery_policy(DESIGN_APPLY_OPERATION_KIND) == "automatic"


@pytest.mark.parametrize(
    "settings",
    [
        {},
        _settings() | {"authored_style": ""},
        _settings() | {"vision_qa": "yes"},
        _settings() | {"source_revision": -1},
    ],
)
def test_template_apply_rejects_invalid_target_owned_metadata(settings):
    handler = WorkspaceAuthoredTemplateOperationHandler(
        generation_handler=_GenerationHandler()
    )

    with pytest.raises(NonRetryableWorkspaceOperationError) as captured:
        asyncio.run(handler.execute(_Context(expected_revision=0, settings=settings)))

    assert captured.value.code == "authored_template_request_invalid"


def test_template_apply_requires_a_revision_fence():
    handler = WorkspaceAuthoredTemplateOperationHandler(
        generation_handler=_GenerationHandler()
    )

    with pytest.raises(NonRetryableWorkspaceOperationError) as captured:
        asyncio.run(
            handler.execute(
                _Context(expected_revision=None, settings=_settings())
            )
        )

    assert captured.value.code == "workspace_expected_revision_required"


def test_template_apply_delegates_linked_generation_and_reports_target():
    generation_handler = _GenerationHandler()
    handler = WorkspaceAuthoredTemplateOperationHandler(
        generation_handler=generation_handler
    )
    source_id = uuid.uuid4()
    context = _Context(expected_revision=0, settings=_settings(source_id))

    result = asyncio.run(handler.execute(context))

    assert generation_handler.context is context
    assert result.result_revision == 1
    assert result.payload == {
        "status": "completed",
        "kind": "template.apply",
        "source_presentation_id": str(source_id),
        "source_revision": 7,
        "target_presentation_id": str(context.claim.presentation_id),
        "presentation_id": str(context.claim.presentation_id),
        "authored_style": "signal",
    }


def test_design_apply_preserves_canonical_kind_and_resolved_snapshot():
    generation_handler = _GenerationHandler()
    handler = WorkspaceAuthoredTemplateOperationHandler(
        generation_handler=generation_handler
    )
    context = _Context(
        expected_revision=0,
        settings=_design_settings(),
        kind=DESIGN_APPLY_OPERATION_KIND,
    )

    result = asyncio.run(handler.execute(context))

    assert result.payload["kind"] == DESIGN_APPLY_OPERATION_KIND
    assert result.payload["resolved_style_digest"].startswith("sha256:")
    assert result.payload["resolved_master_pin"] is None
    assert result.payload["resolved_composition_pin"] is None


def test_template_apply_rejects_source_owned_operation():
    shared_id = uuid.uuid4()
    context = _Context(expected_revision=7, settings=_settings(shared_id))
    context.claim = replace(context.claim, presentation_id=shared_id)
    handler = WorkspaceAuthoredTemplateOperationHandler(
        generation_handler=_GenerationHandler()
    )

    with pytest.raises(NonRetryableWorkspaceOperationError) as captured:
        asyncio.run(handler.execute(context))

    assert captured.value.code == "authored_template_request_invalid"
