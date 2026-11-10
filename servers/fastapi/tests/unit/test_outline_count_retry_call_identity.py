"""A short outline's retry must be its own provider call, not a replay.

Live SVG generation asked for 10 pages and the first outline came back with 9.
The pipeline asks once more with the exact count, but both outline streams ran
in one ``outline`` usage scope under the same semantic slot, so the retry
reserved the logical call the first stream had already settled. For a provider
without idempotency support the public ledger refuses that reuse with
``public_provider_terminal_replay_unsupported`` and the deck failed; resending
the same request succeeded. The retry now carries its own slot and the retry
class, so a true replay of either call is still refused.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from models.generate_presentation_request import GeneratePresentationRequest
from services import presentation_generation_pipeline as pipeline
from services.provider_pricing import ProviderModelPricing
from services.public_operation_admission import PublicAdmissionRejected
from utils import llm_utils
from utils.llm_calls import generate_presentation_outlines as outlines_module


class _Ledger:
    """The public logical-call ledger's reuse rule, in memory.

    ``reserve_public_provider_call_detached`` keys a row by (operation, stage,
    slide, logical call key) and refuses a reused row outright for a provider
    that cannot replay; this keeps exactly that rule.
    """

    def __init__(self):
        self.rows: dict[tuple, SimpleNamespace] = {}
        self.reservations: list[dict] = []

    async def reserve(self, **kwargs):
        self.reservations.append(kwargs)
        key = (kwargs["operation_id"], kwargs["stage"], kwargs["slide_index"],
               kwargs["logical_call_key"])
        existing = self.rows.get(key)
        if existing is not None:
            if not kwargs["provider_idempotency_capable"]:
                raise PublicAdmissionRejected(
                    "public_provider_inflight_replay_unsupported"
                    if existing.state == "reserved"
                    else "public_provider_terminal_replay_unsupported"
                )
            return existing
        row = SimpleNamespace(
            id=uuid.uuid4(), provider_idempotency_key=f"key-{len(self.rows)}",
            state="reserved", is_terminal_reuse=False,
        )
        self.rows[key] = row
        return row

    async def reconcile(self, lease, *, outcome, **_kwargs):
        lease.state = outcome


def _outline(count: int) -> str:
    return json.dumps({"slides": [
        {"content": f"# Page {index + 1}\n\n- point {index + 1}"}
        for index in range(count)
    ]})


@pytest.fixture
def ledger(monkeypatch):
    ledger = _Ledger()
    monkeypatch.setattr(
        "services.public_operation_admission.reserve_public_provider_call_detached",
        ledger.reserve,
    )
    monkeypatch.setattr(
        "services.public_operation_admission.reconcile_public_provider_call_detached",
        ledger.reconcile,
    )
    monkeypatch.setattr(
        "services.provider_usage_instrumentation.durable_provider_runtime_required",
        lambda: False,
    )

    async def current_fence(_fence):
        return None

    monkeypatch.setattr(
        "services.provider_usage_instrumentation._assert_current_public_operation_fence",
        current_fence,
    )
    monkeypatch.setattr(
        "services.provider_pricing_registry.resolve_public_provider_pricing",
        lambda *, provider_id, model_id: ProviderModelPricing(
            revision="test-pricing-v1", provider_id=provider_id, model_id=model_id,
            input_microunits_per_million_tokens=1,
            output_microunits_per_million_tokens=1,
        ),
    )
    monkeypatch.setattr(
        "services.provider_runtime_job_registration.persisted_request_counts",
        AsyncMock(return_value=(0, 0)),
    )
    monkeypatch.setattr(
        "services.provider_runtime_job_registration.verified_lineage_request_baseline",
        AsyncMock(return_value=(0, 0, None, None)), raising=False,
    )
    # The live provider (Anthropic) has no provider-side idempotency.
    monkeypatch.setattr(llm_utils, "supports_provider_idempotency",
                        lambda *_args, **_kwargs: False)
    monkeypatch.setattr(outlines_module, "get_client", lambda **_kwargs: object())
    monkeypatch.setattr(outlines_module, "get_llm_config", lambda: None)
    monkeypatch.setattr(outlines_module, "get_model", lambda: "test-model")
    monkeypatch.setattr(outlines_module, "get_generate_kwargs", lambda **kwargs: kwargs)
    monkeypatch.setattr(llm_utils, "_provider_receipt_identity", lambda: "anthropic")
    return ledger


def _run_generation(monkeypatch, fake_async_session, ledger, page_counts):
    monkeypatch.setenv("AUTHORED_CONTENT_BRIEF_ENABLED", "false")
    streams = iter(page_counts)

    async def fake_stream(_client, **_kwargs):
        yield SimpleNamespace(type="content", chunk=_outline(next(streams)))

    monkeypatch.setattr(llm_utils, "_stream_generate_events_unadmitted", fake_stream)
    request = GeneratePresentationRequest(
        content="Quarterly business review", n_slides=10, language="English",
        export_as="pptx", strategy="svg",
    )
    operation_id = uuid.uuid4()
    fake_async_session._scalar_result = 1  # the running operation's fencing token
    generation_job = SimpleNamespace(
        id=uuid.uuid4(), attempt_number=1, workspace_operation_id=operation_id,
        attempt_token="attempt-1", request_payload={}, authored_outline=None,
        svg_style_snapshot=None, updated_at=None,
    )
    seen = {}

    async def stop_after_outline(_request, _presentation_id, presentation_outlines, *_a, **_k):
        seen["pages"] = len(presentation_outlines.slides)
        raise RuntimeError("stop after outline")

    with patch.object(
        pipeline.MEM0_PRESENTATION_MEMORY_SERVICE, "store_generation_context", new=AsyncMock(),
    ), patch.object(
        pipeline.CONCURRENT_SERVICE, "run_task", new=Mock(),
    ), patch(
        "services.svg_presentation_service.generate_svg_presentation",
        side_effect=stop_after_outline,
    ):
        # The handler wraps the stop into its error envelope; reaching the
        # authoring step with the outline is what this test needs.
        with pytest.raises(HTTPException) as stopped:
            asyncio.run(pipeline.generate_presentation_handler(
                request=request, presentation_id=uuid.uuid4(), async_status=None,
                generation_job=generation_job, sql_session=fake_async_session,
            ))
    assert stopped.value.detail["code"] == "internal_defect"
    return seen, operation_id


def test_a_short_outline_is_retried_under_its_own_call_identity(
    monkeypatch, fake_async_session, ledger,
):
    seen, operation_id = _run_generation(monkeypatch, fake_async_session, ledger, [9, 10])

    assert seen["pages"] == 10
    outline_calls = [row for row in ledger.reservations if row["stage"] == "outline"]
    assert [row["logical_call_key"] for row in outline_calls] == [
        "stream.primary", "stream.primary.outline-retry",
    ]
    assert [row["retry_class"] for row in outline_calls] == ["initial", "retry"]
    assert {row["operation_id"] for row in outline_calls} == {operation_id}
    assert all(row.state == "succeeded" for row in ledger.rows.values())


def test_a_full_outline_makes_exactly_one_provider_call(
    monkeypatch, fake_async_session, ledger,
):
    seen, _ = _run_generation(monkeypatch, fake_async_session, ledger, [10])

    assert seen["pages"] == 10
    assert [row["logical_call_key"] for row in ledger.reservations
            if row["stage"] == "outline"] == ["stream.primary"]


def test_replaying_the_retry_slot_is_still_refused(monkeypatch, ledger):
    """The new slot is deterministic: the same retry twice is still a replay."""

    from services.provider_usage_instrumentation import (
        provider_usage_scope,
        public_operation_scope,
    )

    async def fake_stream(_client, **_kwargs):
        yield SimpleNamespace(type="content", chunk=_outline(10))

    monkeypatch.setattr(llm_utils, "_stream_generate_events_unadmitted", fake_stream)
    operation_id = uuid.uuid4()

    async def stream(**kwargs):
        async for _event in llm_utils.stream_generate_events(
            object(), model="test-model", **kwargs,
        ):
            pass

    def retry_once():
        return stream(is_retry=True, semantic_slot_suffix=".outline-retry")

    async def scenario():
        with public_operation_scope(operation_id, attempt_token="attempt-1",
                                    fencing_token=1), provider_usage_scope(
            job_id=uuid.uuid4(), operation="authored_generation", stage="outline",
            attempt_number=1,
        ):
            await stream()
            await retry_once()
            with pytest.raises(PublicAdmissionRejected) as refused:
                await retry_once()
        return refused.value.code

    assert asyncio.run(scenario()) == "public_provider_terminal_replay_unsupported"
