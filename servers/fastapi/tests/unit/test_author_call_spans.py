"""SPEED-12: one authored provider call splits into three spans that partition its total."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest

from services.provider_usage_instrumentation import (
    ProviderOperationUsage,
    provider_operation_usage_scope,
    provider_usage_scope,
)
from utils.llm_calls import author_call_spans
from utils.llm_calls.author_call_spans import ProviderCallSpans, record_call_spans


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_spans_partition_the_total_with_a_controlled_clock(monkeypatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(author_call_spans, "monotonic", clock)
    spans = ProviderCallSpans()
    clock.now += 1.0  # rate slot + admission lease
    spans.mark_admitted()
    clock.now += 0.5  # executor queue

    def sdk_call() -> str:
        clock.now += 2.0  # provider round trip
        return "response"

    assert spans.wrap(sdk_call)() == "response"
    spans.finish()
    record = spans.record(output_tokens=400)
    assert record["admissionWaitMillis"] == 1000
    assert record["executorWaitMillis"] == 500
    assert record["providerMillis"] == 2000
    assert record["totalMillis"] == 3500
    assert (
        record["admissionWaitMillis"] + record["executorWaitMillis"] + record["providerMillis"]
        == record["totalMillis"]
    )
    assert record["outputTokens"] == 400
    assert record["outputTokensPerSecond"] == 200.0


def test_spans_without_admission_are_all_waiting_and_carry_no_rate(monkeypatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(author_call_spans, "monotonic", clock)
    spans = ProviderCallSpans()
    clock.now += 0.25
    spans.finish()
    record = spans.record(output_tokens=None)
    assert record == {
        "totalMillis": 250,
        "admissionWaitMillis": 250,
        "executorWaitMillis": 0,
        "providerMillis": 0,
    }


def test_admitted_but_never_dispatched_call_charges_the_executor_wait(monkeypatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(author_call_spans, "monotonic", clock)
    spans = ProviderCallSpans()
    clock.now += 0.1
    spans.mark_admitted()
    clock.now += 0.3  # egress policy raised before the executor ran the call
    spans.finish()
    record = spans.record()
    assert record["admissionWaitMillis"] == 100
    assert record["executorWaitMillis"] == 300
    assert record["providerMillis"] == 0
    assert "outputTokensPerSecond" not in record


def test_fake_admission_and_executor_spans_sum_to_the_total() -> None:
    """A fake admission gate and a real executor hand-off: the three spans
    partition the wall time exactly, and each lands on the right side."""

    @contextlib.asynccontextmanager
    async def fake_admission():
        await asyncio.sleep(0.05)
        yield

    async def run() -> dict[str, object]:
        spans = ProviderCallSpans()
        loop = asyncio.get_running_loop()
        async with fake_admission():
            spans.mark_admitted()
            # Occupy the single executor thread so the hand-off has to wait.
            import concurrent.futures
            import time

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            blocker = loop.run_in_executor(executor, lambda: time.sleep(0.05))
            response = await loop.run_in_executor(
                executor, spans.wrap(lambda: (time.sleep(0.05), "ok")[1])
            )
            await blocker
            executor.shutdown(wait=True)
        spans.finish()
        assert response == "ok"
        return spans.record()

    record = asyncio.run(run())
    assert (
        record["admissionWaitMillis"] + record["executorWaitMillis"] + record["providerMillis"]
        == record["totalMillis"]
    )
    assert record["admissionWaitMillis"] >= 40
    assert record["executorWaitMillis"] >= 40
    assert record["providerMillis"] >= 40


def test_record_call_spans_lands_in_the_active_stage_of_the_usage_ledger() -> None:
    class _Usage:
        completion_tokens = 30

    class _Response:
        usage = _Usage()

    with provider_operation_usage_scope() as usage:
        assert isinstance(usage, ProviderOperationUsage)
        with provider_usage_scope(job_id=uuid.uuid4(), operation="authored_generation", stage="author"):
            spans = ProviderCallSpans()
            spans.mark_admitted()
            spans.wrap(lambda: None)()
            record = record_call_spans(spans, _Response())
        snapshot = usage.call_timing.snapshot()
    assert record["outputTokens"] == 30
    assert snapshot is not None
    assert set(snapshot["stages"]) == {"author"}
    assert snapshot["stages"]["author"]["calls"] == 1


def test_record_call_spans_without_a_ledger_is_harmless() -> None:
    spans = ProviderCallSpans()
    record = record_call_spans(spans, None)
    assert set(record) == {"totalMillis", "admissionWaitMillis", "executorWaitMillis", "providerMillis"}


@pytest.mark.anyio
async def test_author_slide_generate_reports_spans_per_call(monkeypatch) -> None:
    """The authored call site wires the spans: one record per provider call,
    filed under the active stage of the operation's usage ledger."""
    from utils.llm_calls import author_slide

    class _Usage:
        completion_tokens = 12

    class _Response:
        usage = _Usage()
        content = "<html></html>"

    async def no_wait() -> None:
        return None

    @contextlib.asynccontextmanager
    async def admission(**_kwargs):
        yield object()

    monkeypatch.setattr(author_slide, "_wait_for_author_rate_slot", no_wait)
    monkeypatch.setattr(author_slide, "provider_call_admission", admission)
    monkeypatch.setattr(author_slide, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_slide, "current_provider_idempotency_key", lambda: None)
    monkeypatch.setattr(author_slide, "supports_provider_idempotency", lambda *_a, **_k: False)
    monkeypatch.setattr(
        author_slide, "provider_generate_call", lambda *_a, **_k: (lambda: _Response())
    )
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        author_slide, "record_call_spans", lambda spans, response: captured.append(spans.record())
    )

    async def fake_record_provider_call(**_kwargs) -> None:
        return None

    monkeypatch.setattr(author_slide, "record_provider_call", fake_record_provider_call)
    source = author_slide.__file__
    assert source.endswith("author_slide.py")
    # Drive only the inner ``generate`` closure through the public function's
    # own wiring: build the smallest call that reaches it.
    generate = _extract_generate(author_slide)
    response = await generate({"messages": []}, semantic_slot="author.initial")
    assert response.content == "<html></html>"
    assert len(captured) == 1
    record = captured[0]
    assert (
        record["admissionWaitMillis"] + record["executorWaitMillis"] + record["providerMillis"]
        == record["totalMillis"]
    )


def _extract_generate(author_slide):
    """Rebuild the ``generate`` closure exactly as ``author_slide_html`` does.

    The closure is nested inside a large prompt-building function; this test
    exercises the same statements by executing the closure's source with the
    module's (monkeypatched) globals and the free variables it needs.
    """
    import inspect
    import textwrap

    source = inspect.getsource(author_slide.author_slide_html)
    start = source.index("    async def generate(")
    end = source.index("\n    try:\n        response = await generate(", start)
    body = textwrap.dedent(source[start:end])
    namespace: dict[str, object] = {}
    wrapper = (
        "def make(client, model, provider, receipt_retry_class, receipt_attempt_ordinal,"
        " initial_provider_attempt, provider_call_attempt):\n"
        + textwrap.indent(body, "    ")
        + "\n    return generate\n"
    )
    exec(compile(wrapper, "<author_slide.generate>", "exec"), author_slide.__dict__, namespace)
    return namespace["make"](
        client=object(),
        model="test-model",
        provider="unknown",
        receipt_retry_class=None,
        receipt_attempt_ordinal=None,
        initial_provider_attempt=1,
        provider_call_attempt=0,
    )
