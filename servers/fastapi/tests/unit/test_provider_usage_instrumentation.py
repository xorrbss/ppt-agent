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
from services.public_operation_admission import PublicAdmissionRejected
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

    assert response_usage(response) == ("reported", 12, 34, None)


def test_response_usage_missing_or_non_numeric_is_unavailable_not_zero() -> None:
    assert response_usage(SimpleNamespace()) == ("unavailable", None, None, None)
    assert response_usage({"usage": {"input_tokens": "12"}}) == (
        "unavailable",
        None,
        None,
        None,
    )


def test_response_usage_extracts_cached_tokens_from_responses_api_details() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            details={"input_tokens_details": {"cached_tokens": 64}},
        )
    )

    assert response_usage(response) == ("reported", 100, 20, 64)


def test_response_usage_extracts_cached_tokens_from_chat_completions_details() -> None:
    response = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "details": {"prompt_tokens_details": {"cached_tokens": 32}},
        }
    }

    assert response_usage(response) == ("reported", 100, 20, 32)


def test_response_usage_cached_tokens_is_none_when_absent() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, details={})
    )

    assert response_usage(response) == ("reported", 100, 20, None)


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
            # max_total_requests is a JobKillCondition, not a cost budget: the
            # breach cancels the shared job rather than rejecting one call.
            with pytest.raises(CancellationRequested):
                async with provider_call_admission():
                    pytest.fail("recovery attempt reset the durable job request cap")

        status = await provider_runtime_job_status(job_id)
        assert status["job_id"] == str(job_id)
        assert status["total_requests"] == 1
        assert status["cancelled"] is True

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
    assert first.public_logical_call_key("author.primary") == "author.primary"
    assert (
        recovered.public_logical_call_key("author.primary")
        == "author.primary.resume-9"
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


def _priced(provider_id: str, model_id: str) -> ProviderModelPricing:
    """One microunit per token, so an expected cost is exact rather than rounded."""

    return ProviderModelPricing(
        revision="test-pricing-v1",
        provider_id=provider_id,
        model_id=model_id,
        input_microunits_per_million_tokens=1_000_000,
        output_microunits_per_million_tokens=1_000_000,
    )


def test_priced_public_call_enriches_with_the_computed_cost(monkeypatch) -> None:
    """The only reachable public path: a fence implies an exact pinned rate.

    ``public_fence`` gates both the pricing lookup and the public reservation,
    and ``resolve_public_provider_pricing`` fails closed, so a public logical
    call always carries pricing whose identity equals the call's own.
    """

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

        async def receipt(value):
            events.append(("receipt", value))

        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )
        monkeypatch.setattr(
            "services.provider_pricing_registry.resolve_public_provider_pricing",
            lambda **_kwargs: _priced("codex", "test-model"),
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
            # Production call sites hand admission the same identity they later
            # report; the placeholder defaults are never used under a fence.
            async with provider_call_admission(provider="codex", model="test-model"):
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
        assert enrich_kwargs["actual_cost_microunits"] == 12 + 34
        assert enrich_kwargs["input_tokens"] == 12
        assert enrich_kwargs["output_tokens"] == 34
        assert enrich_kwargs["pricing_revision"] == "test-pricing-v1"

    asyncio.run(scenario())


def test_unpriced_local_call_records_tokens_without_inventing_a_cost(
    monkeypatch,
) -> None:
    """Without a public fence there is no rate card, and none may be invented."""

    async def scenario() -> None:
        receipts = []

        async def receipt(value):
            receipts.append(value)

        monkeypatch.delenv("PROVIDER_INPUT_MICROUNITS_PER_1K", raising=False)
        monkeypatch.delenv("PROVIDER_OUTPUT_MICROUNITS_PER_1K", raising=False)
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )
        monkeypatch.setattr(
            "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
            receipt,
        )

        with provider_usage_scope(
            job_id=uuid.uuid4(),
            operation="generate",
            stage="author",
            attempt_number=1,
            slide_index=0,
        ):
            async with provider_call_admission(provider="codex", model="test-model"):
                pass
            await record_provider_call(
                provider="codex",
                model="test-model",
                response=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=12,
                        output_tokens=34,
                        details={"input_tokens_details": {"cached_tokens": 5}},
                    )
                ),
                latency_ms=23,
            )

        assert len(receipts) == 1
        recorded = receipts[0]
        assert recorded.usage_state == "reported"
        assert recorded.input_tokens == 12
        assert recorded.output_tokens == 34
        assert recorded.cached_tokens == 5
        assert recorded.reported_cost_microunits is None
        assert recorded.pricing_version == "unpriced-v1"

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


def test_cached_tokens_reach_the_operation_usage_snapshot(monkeypatch) -> None:
    """R2-D: a provider-served cache hit is counted, never netted off input.

    ``cachedTokens`` is a subset of ``inputTokens`` the provider billed at a
    reduced rate, so the two are reported side by side: subtracting one from
    the other would make a receipt disagree with the invoice.
    """

    from services.provider_usage_instrumentation import (
        provider_operation_usage_scope,
    )

    async def scenario() -> dict:
        async def receipt(_value):
            return None

        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )
        monkeypatch.setattr(
            "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
            receipt,
        )

        with provider_operation_usage_scope() as usage:
            with provider_usage_scope(
                job_id=uuid.uuid4(),
                operation="generate",
                stage="author",
                attempt_number=1,
                slide_index=0,
            ):
                async with provider_call_admission(
                    provider="codex", model="test-model"
                ):
                    pass
                await record_provider_call(
                    provider="codex",
                    model="test-model",
                    response=SimpleNamespace(
                        usage=SimpleNamespace(
                            input_tokens=1200,
                            output_tokens=340,
                            details={"input_tokens_details": {"cached_tokens": 900}},
                        )
                    ),
                    latency_ms=17,
                )
        return usage.snapshot()

    snapshot = asyncio.run(scenario())

    assert snapshot["totals"]["cachedTokens"] == 900
    assert snapshot["totals"]["inputTokens"] == 1200
    assert snapshot["stages"]["author"]["cachedTokens"] == 900


def test_a_provider_without_a_cache_counter_reports_zero_cached_tokens(
    monkeypatch,
) -> None:
    from services.provider_usage_instrumentation import (
        provider_operation_usage_scope,
    )

    async def scenario() -> dict:
        async def receipt(_value):
            return None

        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: False,
        )
        monkeypatch.setattr(
            "services.provider_usage_receipt_service.record_provider_usage_receipt_detached",
            receipt,
        )

        with provider_operation_usage_scope() as usage:
            with provider_usage_scope(
                job_id=uuid.uuid4(),
                operation="generate",
                stage="author",
                attempt_number=1,
            ):
                async with provider_call_admission(
                    provider="codex", model="test-model"
                ):
                    pass
                await record_provider_call(
                    provider="codex",
                    model="test-model",
                    response=SimpleNamespace(
                        usage=SimpleNamespace(input_tokens=10, output_tokens=2)
                    ),
                    latency_ms=3,
                )
        return usage.snapshot()

    snapshot = asyncio.run(scenario())

    assert snapshot["totals"]["cachedTokens"] == 0
    assert snapshot["totals"]["inputTokens"] == 10


def test_outer_cancellation_during_the_reconcile_still_makes_the_row_terminal(
    monkeypatch,
) -> None:
    """The settlement after a cancelled SDK call is shielded.

    A user closing the outline stream cancels the request task while the
    brief's own timeout is already being reconciled.  Unshielded, that second
    cancellation aborted the reconcile itself and the reservation stayed
    ``reserved`` until the orphan sweeper ran.
    """

    async def scenario() -> None:
        reconciliations = []
        reconcile_started = asyncio.Event()
        release_reconcile = asyncio.Event()
        inside_call = asyncio.Event()

        async def reserve(**kwargs):
            await asyncio.sleep(0)
            return SimpleNamespace(
                id=kwargs["logical_call_key"],
                provider_idempotency_key="stable-provider-key",
                state="reserved",
                is_terminal_reuse=False,
            )

        async def reconcile(lease, **kwargs):
            reconcile_started.set()
            await release_reconcile.wait()
            reconciliations.append((lease.id, kwargs["outcome"]))

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

        async def request() -> None:
            with public_operation_scope(
                uuid.uuid4(), attempt_token="attempt-1", fencing_token=1
            ), provider_usage_scope(
                job_id=uuid.uuid4(),
                operation="generate",
                stage="content_brief",
                attempt_number=1,
            ):
                async with provider_call_admission(semantic_slot="structured.initial"):
                    inside_call.set()
                    await asyncio.Event().wait()  # the SDK call, never finishing

        task = asyncio.create_task(request())
        await inside_call.wait()
        task.cancel()  # the stage deadline
        await reconcile_started.wait()
        task.cancel()  # the outer request goes away while the reconcile runs
        await asyncio.sleep(0)
        release_reconcile.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Let the shielded settlement finish if the task already unwound.
        for _ in range(10):
            if reconciliations:
                break
            await asyncio.sleep(0)

        assert reconciliations == [("structured.initial", "cancelled")]

    asyncio.run(scenario())


def test_pre_sdk_retry_admission_rejection_does_not_trip_provider_circuit(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        settled = []
        job_id = uuid.uuid4()

        # The runtime guard permits a retry only after one initial request.
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="author",
            attempt_number=1,
            slide_index=0,
        ):
            async with provider_call_admission():
                pass

        async def reject(**_kwargs):
            raise PublicAdmissionRejected("public_provider_retry_limit_exceeded")

        async def settle(**kwargs):
            settled.append(kwargs["durable_outcome"])
            await kwargs["lease"].finish(
                actual_cost_microunits=None,
                outcome=kwargs["durable_outcome"],
            )

        async def current_fence(_fence):
            return None

        monkeypatch.setattr(
            "services.public_operation_admission.reserve_public_provider_call_detached",
            reject,
        )
        monkeypatch.setattr(
            "services.provider_usage_instrumentation._settle_provider_call", settle
        )
        monkeypatch.setattr(
            "services.provider_usage_instrumentation._assert_current_public_operation_fence",
            current_fence,
        )

        with public_operation_scope(
            uuid.uuid4(), attempt_token="attempt-2", fencing_token=2
        ), provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="author",
            attempt_number=2,
            slide_index=0,
        ):
            with pytest.raises(PublicAdmissionRejected):
                async with provider_call_admission(
                    is_retry=True, pricing=_unavailable_pricing()
                ):
                    pytest.fail("public budget rejection reached the SDK boundary")

        assert settled == ["failure"]
        status = await provider_runtime_job_status(job_id)
        assert status["consecutive_failures"] == 1

    asyncio.run(scenario())
