from types import SimpleNamespace
import asyncio
import uuid

import pytest

from services.provider_usage_instrumentation import (
    ProviderUsageScope,
    cancel_provider_job,
    provider_call_admission,
    public_operation_scope,
    provider_runtime_job_status,
    provider_receipt_observability_snapshot,
    provider_usage_scope,
    record_provider_call,
    response_usage,
)
from services.provider_runtime_control import (
    BudgetExceededError,
    CancellationRequested,
)
from services.provider_pricing import ProviderModelPricing


def _unavailable_pricing() -> ProviderModelPricing:
    return ProviderModelPricing(
        revision="test-pricing-v1",
        provider_id="unavailable",
        model_id="unavailable",
        input_microunits_per_million_tokens=1,
        output_microunits_per_million_tokens=1,
    )


def test_response_usage_uses_only_standard_numeric_counters() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=34)
    )

    assert response_usage(response) == ("reported", 12, 34)


def test_response_usage_missing_or_non_numeric_is_unavailable_not_zero() -> None:
    assert response_usage(SimpleNamespace()) == ("unavailable", None, None)
    assert response_usage({"usage": {"input_tokens": "12"}}) == (
        "unavailable",
        None,
        None,
    )


def test_receipt_sink_observability_is_scalar_only() -> None:
    snapshot = provider_receipt_observability_snapshot()
    assert set(snapshot) == {"attempted", "persisted", "failed"}
    assert all(isinstance(value, int) and value >= 0 for value in snapshot.values())


def test_actual_call_admission_enforces_budget_before_second_sdk_call(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        job_id = uuid.uuid4()
        monkeypatch.setenv("PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS", "1")
        monkeypatch.setenv("PROVIDER_CALL_ESTIMATED_COST_MICROUNITS", "1")
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="author",
        ):
            async with provider_call_admission():
                pass
            with pytest.raises(BudgetExceededError):
                async with provider_call_admission():
                    pytest.fail("budget gate admitted a second SDK call")
        status = await provider_runtime_job_status(job_id)
        assert status["total_requests"] == 1
        assert status["estimated_consumed_microunits"] == 1

    asyncio.run(scenario())


def test_actual_call_admission_observes_job_cancellation(monkeypatch) -> None:
    async def scenario() -> None:
        job_id = uuid.uuid4()
        monkeypatch.delenv(
            "PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS", raising=False
        )
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="image",
        ):
            async with provider_call_admission():
                pass
            await cancel_provider_job(job_id, "operator_cancelled")
            with pytest.raises(CancellationRequested):
                async with provider_call_admission():
                    pytest.fail("cancelled job reached the SDK boundary")

    asyncio.run(scenario())


def test_operation_attempts_share_one_bounded_runtime_job(monkeypatch) -> None:
    async def scenario() -> None:
        job_id = uuid.uuid4()
        monkeypatch.setenv("PROVIDER_JOB_MAX_TOTAL_REQUESTS", "1")
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="content_brief",
            attempt_number=1,
        ):
            async with provider_call_admission():
                pass

        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="content_brief",
            attempt_number=2,
        ):
            with pytest.raises(BudgetExceededError):
                async with provider_call_admission():
                    pytest.fail("recovery attempt reset the durable job request cap")

        status = await provider_runtime_job_status(job_id)
        assert status["job_id"] == str(job_id)
        assert status["total_requests"] == 1
        assert status["cancelled"] is False

    asyncio.run(scenario())


def test_runtime_request_id_separates_stage_and_deck_from_slide_zero() -> None:
    async def scenario() -> None:
        job_id = uuid.uuid4()
        deck = ProviderUsageScope(job_id, "generate", "outline", 1, None)
        slide_zero = ProviderUsageScope(job_id, "generate", "outline", 1, 0)
        author_zero = ProviderUsageScope(job_id, "generate", "author", 1, 0)

        ids = {
            deck.next_runtime_request_id("primary"),
            slide_zero.next_runtime_request_id("primary"),
            author_zero.next_runtime_request_id("primary"),
        }

        assert len(ids) == 3
        assert any(":deck:" in request_id for request_id in ids)
        assert any(":slide-0:" in request_id for request_id in ids)

    asyncio.run(scenario())


def test_public_logical_call_identity_is_stable_across_worker_attempts() -> None:
    job_id = uuid.uuid4()
    first = ProviderUsageScope(job_id, "generate", "author", 1, 3)
    recovered = ProviderUsageScope(job_id, "generate", "author", 9, 3)

    assert first.public_logical_call_ordinal("author.primary") == (
        recovered.public_logical_call_ordinal("author.primary")
    )
    assert first.next_runtime_request_id("author.primary") != (
        recovered.next_runtime_request_id("author.primary")
    )


def test_public_success_is_terminal_and_independent_scopes_reuse_semantic_call(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        reservations = []
        reconciliations = []

        async def reserve(**kwargs):
            await asyncio.sleep(0)
            reservations.append(kwargs)
            return SimpleNamespace(
                id=kwargs["logical_call_key"],
                provider_idempotency_key="stable-provider-key",
                state="reserved",
                is_terminal_reuse=False,
            )

        async def reconcile(lease, **kwargs):
            await asyncio.sleep(0)
            reconciliations.append((lease, kwargs))

        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )
        monkeypatch.setattr(
            "services.provider_pricing_registry.resolve_public_provider_pricing",
            lambda **_kwargs: _unavailable_pricing(),
        )
        async def current_fence(_fence):
            return None

        monkeypatch.setattr(
            "services.provider_usage_instrumentation._assert_current_public_operation_fence",
            current_fence,
        )
        monkeypatch.setattr(
            "services.public_operation_admission.reserve_public_provider_call_detached",
            reserve,
        )
        monkeypatch.setattr(
            "services.public_operation_admission.reconcile_public_provider_call_detached",
            reconcile,
        )
        operation_id = uuid.uuid4()
        job_id = uuid.uuid4()

        async def one_call() -> None:
            with public_operation_scope(
                operation_id,
                attempt_token="attempt-1",
                fencing_token=1,
            ), provider_usage_scope(
                job_id=job_id,
                operation="generate",
                stage="author",
                attempt_number=1,
                slide_index=0,
            ):
                async with provider_call_admission():
                    pass

        await asyncio.gather(*(one_call() for _ in range(10)))

        assert {row["logical_call_key"] for row in reservations} == {"primary"}
        assert all(row["logical_call_ordinal"] is None for row in reservations)
        assert len(reconciliations) == 10
        assert all(
            kwargs["outcome"] == "succeeded"
            and kwargs["actual_cost_microunits"] is None
            for _, kwargs in reconciliations
        )

    asyncio.run(scenario())


def test_reported_tokens_enrich_terminal_call_without_inventing_price(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        events = []
        public_lease = SimpleNamespace(
            id="logical-call",
            provider_idempotency_key="stable-provider-key",
            state="reserved",
            is_terminal_reuse=False,
        )

        async def reserve(**kwargs):
            events.append(("reserve", kwargs))
            return public_lease

        async def reconcile(lease, **kwargs):
            events.append(("reconcile", lease, kwargs))

        async def enrich(lease, **kwargs):
            events.append(("enrich", lease, kwargs))

        async def receipt(_receipt):
            events.append(("receipt",))

        monkeypatch.delenv("PROVIDER_INPUT_MICROUNITS_PER_1K", raising=False)
        monkeypatch.delenv("PROVIDER_OUTPUT_MICROUNITS_PER_1K", raising=False)
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )
        monkeypatch.setattr(
            "services.provider_pricing_registry.resolve_public_provider_pricing",
            lambda **_kwargs: _unavailable_pricing(),
        )
        async def current_fence(_fence):
            return None

        monkeypatch.setattr(
            "services.provider_usage_instrumentation._assert_current_public_operation_fence",
            current_fence,
        )
        monkeypatch.setattr(
            "services.public_operation_admission.reserve_public_provider_call_detached",
            reserve,
        )
        monkeypatch.setattr(
            "services.public_operation_admission.reconcile_public_provider_call_detached",
            reconcile,
        )
        monkeypatch.setattr(
            "services.public_operation_admission.enrich_public_provider_call_detached",
            enrich,
        )
        monkeypatch.setattr(
            "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
            receipt,
        )

        with public_operation_scope(
            uuid.uuid4(),
            attempt_token="attempt-1",
            fencing_token=1,
        ), provider_usage_scope(
            job_id=uuid.uuid4(),
            operation="generate",
            stage="author",
            attempt_number=1,
            slide_index=0,
        ):
            async with provider_call_admission():
                pass
            await record_provider_call(
                provider="codex",
                model="test-model",
                response=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=12, output_tokens=34)
                ),
                latency_ms=23,
            )

        assert [event[0] for event in events] == [
            "reserve",
            "reconcile",
            "enrich",
            "receipt",
        ]
        enrich_kwargs = events[2][2]
        assert enrich_kwargs["actual_cost_microunits"] is None
        assert enrich_kwargs["input_tokens"] == 12
        assert enrich_kwargs["output_tokens"] == 34

    asyncio.run(scenario())


def test_required_durable_admission_reserves_and_reconciles_before_sdk(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls = []
        durable_lease = object()

        async def reserve(**kwargs):
            calls.append(("reserve", kwargs))
            return durable_lease

        async def reconcile(lease, **kwargs):
            calls.append(("reconcile", lease, kwargs))

        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: True,
        )
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.reserve_provider_call_detached",
            reserve,
        )
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.reconcile_provider_call_detached",
            reconcile,
        )
        job_id = uuid.uuid4()
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="author",
        ):
            async with provider_call_admission(estimated_cost_microunits=7):
                calls.append(("sdk",))

        assert calls[0][0] == "reserve"
        assert calls[0][1]["job_id"] == job_id
        assert calls[0][1]["estimated_cost"] == 7
        assert calls[1] == ("sdk",)
        assert calls[2] == (
            "reconcile",
            durable_lease,
            {"actual_cost": None, "outcome": "success"},
        )

    asyncio.run(scenario())
