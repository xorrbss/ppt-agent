import asyncio
import uuid
from functools import wraps
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_from_template import (
    AuthoredPageRemakePreviewRequest,
    AuthoredPageRemakePublishRequest,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
)
from services.workspace_page_remake_operation_handler import (
    WorkspacePageRemakeOperationHandler,
    register_workspace_page_remake_operation_handler,
)


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class _SessionFactory:
    """Give the background preview runner the test-owned session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> "_SessionFactory":
        return self

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc_info) -> bool:
        return False


def test_page_remake_contract_has_no_deck_scope():
    assert AuthoredPageRemakePreviewRequest.model_validate({}).instruction is None
    assert (
        AuthoredPageRemakePublishRequest(preview_task_id="task-preview").preview_task_id
        == "task-preview"
    )
    assert (
        "scope"
        not in AuthoredPageRemakePreviewRequest.model_json_schema()["properties"]
    )
    assert (
        "slide_indices"
        not in AuthoredPageRemakePreviewRequest.model_json_schema()["properties"]
    )
    with pytest.raises(ValidationError):
        AuthoredPageRemakePublishRequest(preview_task_id="x")
    with pytest.raises(ValidationError):
        AuthoredPageRemakePreviewRequest.model_validate({"scope": "all"})


def test_workspace_registration_replaces_generic_slide_regeneration_handler():
    registry = WorkspaceOperationHandlerRegistry()
    registry.register("slide.regenerate", lambda _context: None)
    handler = register_workspace_page_remake_operation_handler(
        registry=registry, replace=True
    )
    assert isinstance(handler, WorkspacePageRemakeOperationHandler)
    assert registry.resolve("slide.regenerate") is handler


@async_test
async def test_workspace_handler_requires_exact_slide_and_preview_task():
    claim = WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        kind="slide.regenerate",
        request_payload={"context": {"slide_indices": [0, 1]}},
        expected_revision=1,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )
    context = SimpleNamespace(claim=claim)
    with pytest.raises(
        NonRetryableWorkspaceOperationError, match="page_remake_request_invalid"
    ):
        await WorkspacePageRemakeOperationHandler().execute(context)


@async_test
async def test_workspace_handler_refuses_a_master_pinned_authored_deck(
    monkeypatch,
):
    claim = WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        owner_scope="owner",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        kind="slide.regenerate",
        request_payload={
            "context": {
                "slide_index": 0,
                "preview_task_id": "task-restored",
                "source": "page_remake_history",
            }
        },
        expected_revision=3,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )
    presentation = SimpleNamespace(
        revision=3,
        theme={
            "authored_vnext": {
                "schemaVersion": "legacy-authored-vnext-projection/v1",
                "masterPin": {
                    "masterId": "master:company",
                    "masterRevision": 7,
                    "masterDigest": f"sha256:{'a' * 64}",
                },
            }
        },
    )

    class _Session:
        async def scalar(self, _statement):
            return presentation

    class _SessionContext:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, _type, _value, _traceback):
            return False

    monkeypatch.setattr(
        "services.workspace_page_remake_operation_handler.async_session_maker",
        lambda: _SessionContext(),
    )

    async def progress(**_kwargs):
        return True

    async def raise_if_cancelled():
        return None

    context = SimpleNamespace(
        claim=claim,
        progress=progress,
        raise_if_cancelled=raise_if_cancelled,
    )
    with pytest.raises(
        NonRetryableWorkspaceOperationError,
        match="authored_page_remake_unsupported_presentation",
    ):
        await WorkspacePageRemakeOperationHandler().execute(context)


def _svg_preview_handler_fixture(monkeypatch, *, ready=False, witness=None):
    from services import workspace_authored_preview_operation_handlers as handler

    owner, presentation_id, operation_id, job_id = (uuid.uuid4() for _ in range(4))
    claim = WorkspaceOperationClaim(
        operation_id=operation_id, owner_id=owner, owner_scope=str(owner),
        conversation_id=uuid.uuid4(), presentation_id=presentation_id,
        kind="slide.regenerate.preview", expected_revision=3,
        attempt_number=2, attempt_token="current-attempt", fencing_token=4,
        request_payload={"principal_tenant_id": "tenant-preview",
            "provider_usage_job_id": str(job_id),
            "context": {"slide_index": 0, "instruction": "Preserve the diagram"}},
    )
    task = SimpleNamespace(id=handler._page_task_id(operation_id), owner_id=owner,
        status="completed" if ready else "pending", error=None,
        data={"phase": "preview_ready"} if ready else None)
    presentation = SimpleNamespace(id=presentation_id, owner_id=owner)
    slide = SimpleNamespace(is_svg=lambda: True)
    admission = SimpleNamespace(id=uuid.uuid4(), tenant_id="tenant-preview",
        state="active", slide_count=1, planned_request_limit=1, retry_request_limit=0)
    job = SimpleNamespace(id=job_id)
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[presentation, slide, admission, job, witness]),
        get=AsyncMock(return_value=task),
    )
    monkeypatch.setattr(handler, "async_session_maker", _SessionFactory(session))
    context = SimpleNamespace(claim=claim, progress=AsyncMock(return_value=True),
        raise_if_cancelled=AsyncMock(), extend_lease=AsyncMock(return_value=True))
    return handler, context, task, session, admission


@async_test
async def test_svg_preview_binds_original_job_and_real_fence_without_resume_purchase(monkeypatch):
    from services.provider_usage_instrumentation import _ACTIVE_SCOPE, _PUBLIC_OPERATION_FENCE
    from services.workspace_operation_principal import operation_principal

    handler, context, task, session, _ = _svg_preview_handler_fixture(monkeypatch)
    observed = []

    async def run_preview(*args):
        scope, fence = _ACTIVE_SCOPE.get(), _PUBLIC_OPERATION_FENCE.get()
        observed.append((scope.job_id, scope.operation, scope.stage, scope.attempt_number,
                         fence.operation_id, fence.attempt_token, fence.fencing_token))
        task.status, task.data = "completed", {"phase": "preview_ready"}

    monkeypatch.setattr(handler, "_run_preview_background", run_preview)
    with operation_principal(context.claim.owner_id, "tenant-preview"):
        result = await handler.WorkspacePageRemakePreviewOperationHandler().execute(context)
    assert result.payload["status"] == "completed"
    assert observed == [(uuid.UUID(context.claim.request_payload["provider_usage_job_id"]),
        f"svg_page_preview_{context.claim.operation_id.hex}", "author", 1,
        context.claim.operation_id, "current-attempt", 4)]
    assert session.scalar.await_count == 5
    assert _ACTIVE_SCOPE.get() is None and _PUBLIC_OPERATION_FENCE.get() is None


@pytest.mark.parametrize("witness_state", ["reserved", "reported", "failed", "unavailable"])
@async_test
async def test_svg_preview_existing_dispatch_witness_blocks_provider_replay(monkeypatch, witness_state):
    from services.workspace_operation_principal import operation_principal

    handler, context, _, _, _ = _svg_preview_handler_fixture(
        monkeypatch, witness=SimpleNamespace(id=uuid.uuid4(), state=witness_state))
    run_preview = AsyncMock()
    monkeypatch.setattr(handler, "_run_preview_background", run_preview)
    with operation_principal(context.claim.owner_id, "tenant-preview"):
        with pytest.raises(NonRetryableWorkspaceOperationError,
                           match="svg_preview_provider_recovery_required"):
            await handler.WorkspacePageRemakePreviewOperationHandler().execute(context)
    run_preview.assert_not_awaited()


@pytest.mark.parametrize("conflict", ["owner", "tenant", "admission", "task_owner"])
@async_test
async def test_svg_preview_rejects_cross_owner_and_tenant_before_provider(monkeypatch, conflict):
    from services.workspace_operation_principal import operation_principal

    handler, context, task, _, admission = _svg_preview_handler_fixture(monkeypatch)
    owner, tenant = context.claim.owner_id, "tenant-preview"
    if conflict == "owner":
        owner = uuid.uuid4()
    if conflict == "tenant":
        tenant = "other-tenant"
    if conflict == "admission":
        admission.tenant_id = "other-tenant"
    if conflict == "task_owner":
        task.owner_id = uuid.uuid4()
    run_preview = AsyncMock()
    monkeypatch.setattr(handler, "_run_preview_background", run_preview)
    with operation_principal(owner, tenant):
        with pytest.raises(NonRetryableWorkspaceOperationError,
                           match="workspace_operation_principal_conflict"):
            await handler.WorkspacePageRemakePreviewOperationHandler().execute(context)
    run_preview.assert_not_awaited()


@async_test
async def test_svg_preview_ready_recovery_returns_candidate_without_provider(monkeypatch):
    from services.workspace_operation_principal import operation_principal

    handler, context, _, session, _ = _svg_preview_handler_fixture(monkeypatch, ready=True)
    run_preview = AsyncMock()
    monkeypatch.setattr(handler, "_run_preview_background", run_preview)
    with operation_principal(context.claim.owner_id, "tenant-preview"):
        result = await handler.WorkspacePageRemakePreviewOperationHandler().execute(context)
    assert result.payload["task_id"] == handler._page_task_id(context.claim.operation_id)
    assert session.scalar.await_count == 4
    run_preview.assert_not_awaited()


@async_test
async def test_svg_preview_background_unknown_failure_is_nonretryable(monkeypatch):
    from services.workspace_operation_principal import operation_principal

    handler, context, task, _, _ = _svg_preview_handler_fixture(monkeypatch)

    async def run_preview(*args):
        task.status = "error"
        task.error = {"detail": "authored_page_remake_failed", "retryable": True}

    monkeypatch.setattr(handler, "_run_preview_background", run_preview)
    with operation_principal(context.claim.owner_id, "tenant-preview"):
        with pytest.raises(NonRetryableWorkspaceOperationError, match="authored_page_remake_failed"):
            await handler.WorkspacePageRemakePreviewOperationHandler().execute(context)


@async_test
async def test_svg_preview_admission_has_exactly_one_author_and_existing_job(monkeypatch):
    from services import public_operation_admission as public
    from services.workspace_authored_async_admission import admit_svg_page_preview_operation
    from services.workspace_operation_principal import operation_principal

    owner, job_id = uuid.uuid4(), uuid.uuid4()
    operation = SimpleNamespace(id=uuid.uuid4(), owner_id=owner,
        request_payload={"principal_tenant_id": "tenant-preview", "context": {"slide_index": 0}})
    presentation = SimpleNamespace(id=uuid.uuid4(), owner_id=owner, theme={})
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[SimpleNamespace(is_svg=lambda: True),
        SimpleNamespace(id=job_id)]), flush=AsyncMock())
    admission = SimpleNamespace(retry_request_limit=5)

    async def admit(session, **kwargs):
        assert kwargs["operation_id"] == operation.id and kwargs["operation_kind"] == "generation"
        plan = kwargs["request_plan"]
        assert plan.slide_count == plan.initial_request_limit == 1
        assert plan.retry_request_limit == plan.image_budget == plan.repair_budget == plan.vqa_budget == 0
        assert kwargs["principal"].owner_id == owner
        return admission

    monkeypatch.setattr(public, "admit_public_operation_compatible_v2", admit)
    with operation_principal(owner, "tenant-preview"):
        assert await admit_svg_page_preview_operation(session, operation=operation, presentation=presentation)
    assert operation.request_payload["provider_usage_job_id"] == str(job_id)
    assert admission.retry_request_limit == 0


@async_test
@pytest.mark.parametrize("mode", ["remeasure_source", "local_svg"])
async def test_remeasure_source_admission_has_zero_author_and_exact_binding(monkeypatch, mode):
    from services import public_operation_admission as public
    from services.workspace_authored_async_admission import admit_svg_page_preview_operation
    from services.workspace_operation_principal import operation_principal
    from services.svg_contract import source_digest
    owner,job_id=uuid.uuid4(),uuid.uuid4()
    svg="<svg>same source</svg>"
    operation=SimpleNamespace(id=uuid.uuid4(),owner_id=owner,expected_revision=3,
        request_payload={"principal_tenant_id":"tenant-preview","context":{"slide_index":0,
            "mode":mode,"source_digest":source_digest(svg),
            **({"svg_content":svg} if mode == "local_svg" else {})}})
    presentation=SimpleNamespace(id=uuid.uuid4(),owner_id=owner,revision=3,theme={})
    slide=SimpleNamespace(is_svg=lambda:True,svg_content=svg)
    session=SimpleNamespace(scalar=AsyncMock(side_effect=[slide,SimpleNamespace(id=job_id)]),flush=AsyncMock())
    async def admit(session,**kwargs):
        assert kwargs["request_plan"].initial_request_limit==kwargs["request_plan"].retry_request_limit==0
        return SimpleNamespace(retry_request_limit=5)
    admitted=AsyncMock(side_effect=admit)
    monkeypatch.setattr(public,"admit_public_operation_compatible_v2",admitted)
    with operation_principal(owner,"tenant-preview"):
        await admit_svg_page_preview_operation(session,operation=operation,presentation=presentation)
        operation.request_payload["context"]["source_digest"]="sha256:"+"0"*64
        session.scalar=AsyncMock(return_value=slide)
        with pytest.raises(Exception,match="svg_page_remake_source_changed"):
            await admit_svg_page_preview_operation(session,operation=operation,presentation=presentation)
    admitted.assert_awaited_once()


@async_test
@pytest.mark.parametrize("mode", ["remeasure_source", "local_svg"])
async def test_remeasure_source_handler_creates_no_provider_scope(monkeypatch, mode):
    from services.provider_usage_instrumentation import _ACTIVE_SCOPE,_PUBLIC_OPERATION_FENCE
    handler,context,task,_,admission=_svg_preview_handler_fixture(monkeypatch)
    monkeypatch.setattr(handler,"_assert_preview_not_dispatched",AsyncMock())
    observed=[]
    async def run_preview(*args):
        observed.append((_ACTIVE_SCOPE.get(),_PUBLIC_OPERATION_FENCE.get()))
    monkeypatch.setattr(handler,"_run_preview_background",run_preview)
    await handler._run_fenced_svg_preview(context,(admission.id,uuid.uuid4()),0,
        AuthoredPageRemakePreviewRequest(mode=mode,source_digest="sha256:"+"0"*64,
            svg_content="<svg/>" if mode == "local_svg" else None),task.id)
    assert observed==[(None,None)]


@async_test
async def test_remeasure_rejected_candidate_evidence_is_failed_and_unpublishable(monkeypatch):
    from services import page_remake_core as service
    task=SimpleNamespace(id="candidate-task",status="pending",data=None)
    session=SimpleNamespace(get=AsyncMock(return_value=task),rollback=AsyncMock())
    monkeypatch.setattr(service,"async_session_maker",_SessionFactory(session))
    evidence={"phase":"preview_ready","candidate_svg":"<svg>paid failure</svg>",
        "svg_quality":{"passed":False},"provider_receipt":{"requested_model":"fixture"}}
    failed=service.AuthoredPageRemakeError("svg_source_classification_missing",stage="source_quality",
        preview_evidence=evidence)
    monkeypatch.setattr(service,"run_authored_page_remake_preview",AsyncMock(side_effect=failed))
    saved=AsyncMock()
    monkeypatch.setattr(service,"_set_task",saved)
    await service._run_preview_background(uuid.uuid4(),0,AuthoredPageRemakePreviewRequest(),task.id)
    result=saved.await_args.kwargs
    assert result["status"]=="error" and result["data"]["phase"]=="failed"
    assert result["data"]["candidate_svg"]==evidence["candidate_svg"]
    assert result["error"]=={"detail":"svg_source_classification_missing","stage":"source_quality","retryable":False}


@async_test
async def test_svg_preview_separate_operations_append_distinct_accounting_keys(monkeypatch):
    from dataclasses import replace
    from services.provider_usage_instrumentation import _ACTIVE_SCOPE

    handler, context, _, _, admission = _svg_preview_handler_fixture(monkeypatch)
    monkeypatch.setattr(handler, "_assert_preview_not_dispatched", AsyncMock())
    observed = []

    async def run_preview(*args):
        scope = _ACTIVE_SCOPE.get()
        request_id = scope.next_runtime_request_id("svg-author-0-1")
        scope._receipt_request_ids.append(request_id)
        observed.append((request_id, scope.next_receipt_key_hash("provider"),
                         scope.public_logical_call_key("svg-author-0-1")))

    monkeypatch.setattr(handler, "_run_preview_background", run_preview)
    binding = admission.id, uuid.UUID(context.claim.request_payload["provider_usage_job_id"])
    await handler._run_fenced_svg_preview(context, binding, 0,
        AuthoredPageRemakePreviewRequest(), "first-task")
    context.claim = replace(context.claim, operation_id=uuid.uuid4())
    await handler._run_fenced_svg_preview(context, binding, 0,
        AuthoredPageRemakePreviewRequest(), "second-task")
    assert observed[0][0] != observed[1][0]
    assert observed[0][1] != observed[1][1]
    assert observed[0][2] == observed[1][2] == "svg-author-0-1"


@async_test
async def test_svg_preview_legacy_poll_resubmission_keeps_stable_admission(monkeypatch):
    from services import workspace_authored_async_admission as admission
    from services import workspace_service

    operation = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    task = SimpleNamespace(id=f"workspace-page-preview-{operation.id}")
    session = SimpleNamespace(get=AsyncMock(return_value=task))
    keys = []

    async def create_command(session, **kwargs):
        keys.append(kwargs["request"].idempotency_key)
        return operation, len(keys) > 1

    monkeypatch.setattr(workspace_service, "create_workspace_command", create_command)
    monkeypatch.setattr(admission, "wake_workspace_operation_runner", lambda: None)
    values = dict(presentation_id=uuid.uuid4(), operation_kind="slide.regenerate.preview",
        expected_revision=3, command="Preview", context={"slide_index": 0},
        task_id_prefix="workspace-page-preview", pending_message="Queued")
    first = await admission.admit_workspace_authored_task(session, **values)
    second = await admission.admit_workspace_authored_task(session, **values)
    assert first is second is task and keys[0] == keys[1]
