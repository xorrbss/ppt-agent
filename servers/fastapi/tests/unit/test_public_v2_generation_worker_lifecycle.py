from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
import time
import uuid

import pytest
from fastapi import HTTPException

from api.v2.contracts import GenerationRouteReceiptV1
from api.v2.orchestration import PublicV2OrchestrationError
from services.authored_vnext.narrative_ir.content_graph import (
    seal_content_integrity_receipt,
)
from services.authored_vnext.narrative_ir.contracts import model_payload
from services.presentation_generation_job_service import (
    GenerationCapacityUnavailable,
)
from services.public_generation_worker_lifecycle import (
    PUBLIC_OPERATION_CONTRACT_VERSION,
    clamp_retry_to_public_deadline,
    claim_linked_generation_job,
    durable_generation_failure,
    require_public_generation_publication,
)
from services.public_v2_orchestrator import require_generation_content_integrity_for_export
from services.public_v2_operation_lifecycle import is_public_operation_authority
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from services.workspace_operation_retry_policy import WorkspaceOperationRetryDecision
from workers import public_worker_runtime


class _Session:
    def __init__(self, operation):
        self.operation = operation

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _operation_id, **_kwargs):
        return self.operation


class _SessionFactory:
    def __init__(self, operation):
        self.operation = operation

    def __call__(self):
        return _Session(self.operation)


class _Context:
    def __init__(self, claim):
        self.claim = claim
        self.progress_stages: list[str] = []

    async def progress(self, *, stage: str, **_kwargs) -> bool:
        self.progress_stages.append(stage)
        return True

    async def raise_if_cancelled(self) -> None:
        return None


def _claim():
    return SimpleNamespace(
        operation_id=uuid.uuid4(),
        attempt_token="same-attempt-token",
        attempt_number=2,
        fencing_token=7,
    )


def _digest(payload: object) -> str:
    return "sha256:" + sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _operation(claim, *, state: str = "running", result_revision=None, result=None):
    return SimpleNamespace(
        id=claim.operation_id,
        state=state,
        attempt_token=claim.attempt_token,
        attempt_number=claim.attempt_number,
        fencing_token=claim.fencing_token,
        contract_version=PUBLIC_OPERATION_CONTRACT_VERSION,
        execution_profile_version="public-generation-export/v2",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        result_revision=result_revision,
        result=result,
    )


def test_public_capacity_wait_keeps_one_attempt_and_fence(monkeypatch):
    async def scenario():
        claim = _claim()
        context = _Context(claim)
        operation = _operation(claim)
        observed: list[tuple[uuid.UUID, str, int]] = []

        async def claim_linked(operation_id, attempt_token, attempt_number):
            observed.append((operation_id, attempt_token, attempt_number))
            if len(observed) == 1:
                raise GenerationCapacityUnavailable(
                    retry_after_seconds=0.1,
                    queue_position=2,
                )
            return uuid.uuid4(), "task-id"

        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr(
            "services.public_generation_worker_lifecycle.asyncio.sleep",
            no_sleep,
        )
        linked = await claim_linked_generation_job(
            session_factory=_SessionFactory(operation),
            context=context,
            claim_linked=claim_linked,
        )
        assert linked[1] == "task-id"
        assert observed == [
            (claim.operation_id, claim.attempt_token, claim.attempt_number),
            (claim.operation_id, claim.attempt_token, claim.attempt_number),
        ]
        assert context.progress_stages == ["waiting_for_capacity"]

    asyncio.run(scenario())


def test_public_success_requires_durable_publication_witness():
    async def scenario():
        claim = _claim()
        context = _Context(claim)
        running = _operation(claim)
        with pytest.raises(NonRetryableWorkspaceOperationError) as missing:
            await require_public_generation_publication(
                session=_Session(running),
                context=context,
            )
        assert missing.value.code == "public_generation_publication_missing"

        succeeded = _operation(
            claim,
            state="succeeded",
            result_revision=3,
            result={"artifact_id": "durable-artifact"},
        )
        assert (
            await require_public_generation_publication(
                session=_Session(succeeded),
                context=context,
            )
        ) is succeeded

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            HTTPException(
                status_code=401,
                detail={"code": "provider_authentication_failed"},
            ),
            ("provider_authentication_failed", False),
        ),
        (
            HTTPException(status_code=429, detail="provider_rate_limited"),
            ("provider_rate_limited", True),
        ),
    ],
)
def test_provider_failure_is_durable_and_not_a_fake_success(error, expected):
    assert durable_generation_failure(error) == expected


def test_retry_that_cannot_meet_absolute_deadline_terminalizes_now():
    now = datetime.now(timezone.utc)
    admission = SimpleNamespace(deadline_at=now + timedelta(seconds=12))
    decision = WorkspaceOperationRetryDecision(
        state="queued",
        retry_count=1,
        backoff_seconds=5,
        next_attempt_at=now + timedelta(seconds=5),
    )
    terminal, missed = clamp_retry_to_public_deadline(
        deadline_at=admission.deadline_at,
        decision=decision,
        current_retry_count=0,
    )
    assert missed is True
    assert terminal.state == "deadline_exceeded"


def test_public_compatibility_claim_cannot_outlive_absolute_deadline(monkeypatch):
    async def scenario():
        claim = _claim()
        context = _Context(claim)
        operation = _operation(claim)
        operation.deadline_at = datetime.now(timezone.utc) + timedelta(seconds=0.03)
        cancelled = asyncio.Event()

        async def never_claimed(*_args):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        monkeypatch.setattr(
            "services.public_generation_worker_lifecycle.PUBLIC_CAPACITY_COMMIT_RESERVE_SECONDS",
            0.0,
        )
        with pytest.raises(NonRetryableWorkspaceOperationError) as raised:
            await claim_linked_generation_job(
                session_factory=_SessionFactory(operation),
                context=context,
                claim_linked=never_claimed,
            )
        assert raised.value.code == "public_deadline_exceeded"
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_isolated_worker_executor_cannot_outlive_sealed_job_deadline():
    async def scenario():
        cancelled = asyncio.Event()

        async def executor(_job, _store):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

        job = SimpleNamespace(deadline_epoch_seconds=time.time() + 0.03)
        with pytest.raises(public_worker_runtime.PublicWorkerExecutionError) as raised:
            await public_worker_runtime._execute_before_job_deadline(
                job=job,
                executor=executor,
                store=object(),
            )
        assert raised.value.code == "public_worker_job_expired"
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_export_gate_rejects_content_integrity_receipt_from_another_operation():
    operation_id = uuid.uuid4()
    route_unsigned = {
        "schemaVersion": "generation-route-receipt/v1",
        "requestedMode": "registered-template",
        "selectedRoute": "source-native",
        "designPackPin": {
            "designPackId": "pack-1",
            "version": 1,
            "digest": "sha256:" + "1" * 64,
        },
        "narrativeIrDigest": None,
        "fallbackReason": None,
        "outputCapability": "fidelity",
    }
    route = GenerationRouteReceiptV1.model_validate(
        {**route_unsigned, "receiptDigest": _digest(route_unsigned)}
    )
    unit_digest = "sha256:" + "a" * 64
    receipt = seal_content_integrity_receipt(
        operation_id=operation_id,
        source_revision=7,
        result_revision=8,
        generation_lane="new-authoring",
        content_graph_digest="sha256:" + "b" * 64,
        route_strategy_receipt_digest=route.receipt_digest,
        narrative_digest="sha256:" + "c" * 64,
        pages=[
            {
                "pageIndex": 0,
                "expectedContentUnitIds": ["unit-1"],
                "boundContentUnitIds": ["unit-1"],
                "renderedContentUnitIds": ["unit-1"],
                "expectedDigest": unit_digest,
                "boundDigest": unit_digest,
                "renderedDigest": unit_digest,
                "sourceOwnershipDigest": "sha256:" + "d" * 64,
            }
        ],
    )
    operation = SimpleNamespace(
        id=operation_id,
        request_payload={
            "generation_route_receipt": route.model_dump(mode="json", by_alias=True),
            "narrative_checkpoint": {
                "operationId": str(operation_id),
                "narrative": {"contentGraphDigest": "sha256:" + "b" * 64},
            },
        },
        state="succeeded",
        expected_revision=7,
        result_revision=8,
        result={"contentIntegrityReceipt": model_payload(receipt)},
    )
    require_generation_content_integrity_for_export(operation, source_revision=8)

    operation.request_payload["narrative_checkpoint"]["narrative"] = {
        "contentGraphDigest": "sha256:" + "e" * 64
    }
    with pytest.raises(PublicV2OrchestrationError) as graph_replay:
        require_generation_content_integrity_for_export(operation, source_revision=8)
    assert graph_replay.value.reason_code == "content_integrity_receipt_required"

    operation.request_payload["narrative_checkpoint"]["narrative"] = {
        "contentGraphDigest": "sha256:" + "b" * 64
    }
    operation.id = uuid.uuid4()
    with pytest.raises(PublicV2OrchestrationError) as rejected:
        require_generation_content_integrity_for_export(operation, source_revision=8)
    assert rejected.value.reason_code == "content_integrity_receipt_required"


def test_public_projection_rejects_split_operation_and_admission_states():
    now = datetime.now(timezone.utc)
    operation = SimpleNamespace(
        state="succeeded",
        contract_version=PUBLIC_OPERATION_CONTRACT_VERSION,
        execution_profile_version="public-generation-export/v2",
        deadline_at=now + timedelta(minutes=5),
        fencing_token=9,
    )
    active_admission = SimpleNamespace(
        state="active",
        deadline_at=operation.deadline_at,
        fence=9,
    )
    succeeded_admission = SimpleNamespace(
        state="succeeded",
        deadline_at=operation.deadline_at,
        fence=9,
    )
    assert not is_public_operation_authority(
        operation,
        active_admission,
    )
    assert is_public_operation_authority(
        operation,
        succeeded_admission,
    )
