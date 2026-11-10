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

from services.presentation_generation_job_service import (
    WorkspaceLinkedGenerationProjectionPoisoned,
)
from services.public_generation_worker_lifecycle import (
    PUBLIC_OPERATION_CONTRACT_VERSION,
    clamp_retry_to_public_deadline,
    claim_linked_generation_job,
    durable_generation_failure,
    require_public_generation_publication,
)
from services.public_v2_operation_lifecycle import is_public_operation_authority
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from services.workspace_operation_runner import _public_retry_budget_error
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


def test_public_linked_claim_uses_projection_once_without_capacity_polling():
    async def scenario():
        claim = _claim()
        context = _Context(claim)
        operation = _operation(claim)
        observed: list[tuple[uuid.UUID, str, int]] = []

        async def claim_linked(operation_id, attempt_token, attempt_number):
            observed.append((operation_id, attempt_token, attempt_number))
            return uuid.uuid4(), "task-id"

        linked = await claim_linked_generation_job(
            session_factory=_SessionFactory(operation),
            context=context,
            claim_linked=claim_linked,
        )
        assert linked[1] == "task-id"
        assert observed == [
            (claim.operation_id, claim.attempt_token, claim.attempt_number),
        ]
        assert context.progress_stages == []

    asyncio.run(scenario())


def test_poisoned_projection_signals_generic_terminal_path_without_blocking_next_job():
    async def scenario():
        first_claim = _claim()
        first_context = _Context(first_claim)

        async def poisoned(*_args):
            raise WorkspaceLinkedGenerationProjectionPoisoned(
                "authored_slide_capacity_exceeded"
            )

        with pytest.raises(NonRetryableWorkspaceOperationError) as failure:
            await claim_linked_generation_job(
                session_factory=_SessionFactory(_operation(first_claim)),
                context=first_context,
                claim_linked=poisoned,
            )
        assert failure.value.code == "authored_slide_capacity_exceeded"

        next_claim = _claim()
        next_context = _Context(next_claim)
        next_job_id = uuid.uuid4()

        async def healthy(*_args):
            return next_job_id, "next-task"

        assert await claim_linked_generation_job(
            session_factory=_SessionFactory(_operation(next_claim)),
            context=next_context,
            claim_linked=healthy,
        ) == (next_job_id, "next-task")

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


def test_retry_request_budget_terminalizes_before_requeue():
    admission = SimpleNamespace(
        state="active",
        operation_kind="generation",
        initial_request_count=3,
        planned_request_limit=3,
        retry_request_count=2,
        retry_request_limit=2,
        estimated_consumed_microunits=10,
        estimated_reserved_microunits=0,
        estimated_cost_limit_microunits=100,
        actual_consumed_microunits=10,
        actual_reserved_microunits=0,
        actual_cost_limit_microunits=100,
    )
    assert _public_retry_budget_error(admission) == (
        "public_provider_retry_limit_exceeded"
    )


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
