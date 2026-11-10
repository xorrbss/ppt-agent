import asyncio
import uuid

import pytest

import services.workspace_svg_header_design_operation_handler as header_handler
from services.workspace_authored_template_operation_handler import (
    DESIGN_APPLY_OPERATION_KIND,
    TEMPLATE_APPLY_OPERATION_KIND,
    WorkspaceAuthoredTemplateOperationHandler,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationResult,
)


class _Context:
    def __init__(self, *, kind: str, payload: dict):
        self.claim = WorkspaceOperationClaim(
            operation_id=uuid.uuid4(),
            owner_id=None,
            owner_scope="anonymous:test",
            conversation_id=uuid.uuid4(),
            presentation_id=uuid.uuid4(),
            kind=kind,
            request_payload=payload,
            expected_revision=3,
            attempt_number=1,
            attempt_token="attempt-token",
            fencing_token=1,
        )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (TEMPLATE_APPLY_OPERATION_KIND, {"generation_settings": {"authored_style": "signal"}}),
        (DESIGN_APPLY_OPERATION_KIND, {"generation_settings": {"authored_style": "signal"}}),
        (DESIGN_APPLY_OPERATION_KIND, {"context": {"target": "body"}}),
    ],
)
def test_authored_redesign_is_refused_as_removed(kind, payload):
    with pytest.raises(NonRetryableWorkspaceOperationError) as refused:
        asyncio.run(
            WorkspaceAuthoredTemplateOperationHandler().execute(
                _Context(kind=kind, payload=payload)
            )
        )

    assert refused.value.code == "authored_strategy_removed"


def test_common_header_design_goes_to_the_svg_header_handler(monkeypatch):
    seen = []

    class _HeaderHandler:
        async def execute(self, context):
            seen.append(context)
            return WorkspaceOperationResult(payload={"kind": "design.apply"}, result_revision=4)

    monkeypatch.setattr(header_handler, "WorkspaceSvgHeaderDesignOperationHandler", _HeaderHandler)
    context = _Context(kind=DESIGN_APPLY_OPERATION_KIND, payload={"context": {"target": "common_header"}})

    result = asyncio.run(WorkspaceAuthoredTemplateOperationHandler().execute(context))

    assert seen == [context]
    assert result.result_revision == 4
