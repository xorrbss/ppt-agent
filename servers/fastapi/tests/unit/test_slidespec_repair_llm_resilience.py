import asyncio

import pytest

from services.slidespec_pipeline_performance import NegativeProposalCache, ProposalCacheKey
from services.slidespec_repair_pipeline import ProposalBatch
from utils.llm_calls import propose_slidespec_repair as adapter
from utils.llm_calls.slidespec_repair_resilience import (
    RepairLlmCacheKey,
    RepairLlmCircuitBreaker,
    RepairLlmIdentity,
    RepairLlmNegativeCache,
    canonicalize_issue_code,
)


def _identity(**updates: str) -> RepairLlmIdentity:
    values = {
        "provider": "openai",
        "model": "gpt-test",
        "schema_version": "schema/1",
        "prompt_version": "prompt/1",
    }
    values.update(updates)
    return RepairLlmIdentity(**values)


def test_issue_enum_canonicalization_is_closed_and_prompt_drops_unknown() -> None:
    assert canonicalize_issue_code("overlap") == "dom.unintended_overlap"
    assert canonicalize_issue_code("constraint-readability") == (
        "constraint.readability.violated"
    )
    assert canonicalize_issue_code("made_up_issue") is None

    messages, _ = adapter.build_messages(
        {"semanticId": "sem:title"},
        [
            {"code": "overlap", "semanticId": "sem:title"},
            {"code": "made_up_issue", "semanticId": "sem:title"},
        ],
        slide_id="slide-1",
        base_spec_revision=1,
        base_spec_hash="sha256:" + "1" * 64,
        expected_content_digest="sha256:" + "2" * 64,
    )
    prompt = messages[-1].content
    assert "dom.unintended_overlap" in prompt
    assert "made_up_issue" not in prompt


def test_negative_cache_is_ttl_bounded_and_contract_keyed() -> None:
    now = [10.0]
    cache = RepairLlmNegativeCache(
        max_entries=1, ttl_seconds=5, clock=lambda: now[0]
    )
    first = RepairLlmCacheKey(_identity(), "sha256:input-1")
    changed_model = RepairLlmCacheKey(
        _identity(model="gpt-other"), "sha256:input-1"
    )
    assert cache.put(first, "llm_empty")
    assert cache.get(first) == "llm_empty"
    assert cache.get(changed_model) is None
    assert not cache.put(first, "llm_provider_error")
    now[0] = 15.0
    assert cache.get(first) is None


def test_circuit_breaker_opens_on_consecutive_provider_failures_only() -> None:
    now = [20.0]
    breaker = RepairLlmCircuitBreaker(
        failure_threshold=2, cooldown_seconds=10, clock=lambda: now[0]
    )
    identity = _identity()
    breaker.record_failure(identity)
    assert breaker.allow_call(identity)
    breaker.record_failure(identity)
    assert not breaker.allow_call(identity)
    assert breaker.allow_call(_identity(model="independent"))
    now[0] = 30.0
    assert breaker.allow_call(identity)
    breaker.record_success(identity)
    assert breaker.allow_call(identity)


def test_legacy_negative_cache_now_expires() -> None:
    now = [1.0]
    cache = NegativeProposalCache(ttl_seconds=2, clock=lambda: now[0])
    key = ProposalCacheKey("spec", "dom", "capability", "model-contract")
    cache.put(key)
    assert cache.contains(key)
    now[0] = 3.0
    assert not cache.contains(key)


def test_proposal_batch_keeps_bounded_no_candidate_reason() -> None:
    batch = ProposalBatch(
        no_candidate_reason="llm_schema_rejected",
        call_count=1,
        response_count=1,
        schema_rejection_count=1,
    )
    assert batch.plans == ()
    assert batch.no_candidate_reason == "llm_schema_rejected"
    assert batch.schema_rejection_count == 1


def test_adapter_classifies_empty_response_and_reuses_negative_cache(monkeypatch) -> None:
    adapter.DEFAULT_REPAIR_LLM_NEGATIVE_CACHE.clear()
    adapter.DEFAULT_REPAIR_LLM_CIRCUIT_BREAKER.clear()
    calls = 0

    async def generate_once(client, model, messages, **kwargs):
        nonlocal calls
        calls += 1
        return {"plans": []}

    monkeypatch.setattr(adapter, "get_llm_provider", lambda: "provider-a")
    monkeypatch.setattr(adapter, "get_model", lambda: "model-a")
    monkeypatch.setattr(adapter, "get_llm_config", lambda: object())
    monkeypatch.setattr(adapter, "get_client", lambda config: object())
    monkeypatch.setattr(adapter, "_generate_once", generate_once)
    kwargs = {
        "slide_id": "slide-1",
        "base_spec_revision": 1,
        "base_spec_hash": "sha256:" + "1" * 64,
        "expected_content_digest": "sha256:" + "2" * 64,
    }
    first = asyncio.run(
        adapter.propose_slidespec_repair(
            {"semanticId": "sem:title"},
            [{"code": "clipping", "semanticId": "sem:title"}],
            **kwargs,
        )
    )
    second = asyncio.run(
        adapter.propose_slidespec_repair(
            {"semanticId": "sem:title"},
            [{"code": "clipping", "semanticId": "sem:title"}],
            **kwargs,
        )
    )
    assert first.no_candidate_reason == "llm_empty"
    assert first.call_count == first.response_count == first.schema_pass_count == 1
    assert second.no_candidate_reason == "negative_cache_hit"
    assert second.call_count == 0
    assert calls == 1


def test_adapter_circuit_breaker_stops_repeated_provider_calls(monkeypatch) -> None:
    adapter.DEFAULT_REPAIR_LLM_NEGATIVE_CACHE.clear()
    adapter.DEFAULT_REPAIR_LLM_CIRCUIT_BREAKER = RepairLlmCircuitBreaker(
        failure_threshold=2, cooldown_seconds=60
    )
    calls = 0

    async def failing_generate(client, model, messages, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider HTTP 500")

    monkeypatch.setattr(adapter, "get_llm_provider", lambda: "provider-b")
    monkeypatch.setattr(adapter, "get_model", lambda: "model-b")
    monkeypatch.setattr(adapter, "get_llm_config", lambda: object())
    monkeypatch.setattr(adapter, "get_client", lambda config: object())
    monkeypatch.setattr(adapter, "_generate_once", failing_generate)
    kwargs = {
        "slide_id": "slide-1",
        "base_spec_revision": 1,
        "base_spec_hash": "sha256:" + "1" * 64,
        "expected_content_digest": "sha256:" + "2" * 64,
    }
    results = [
        asyncio.run(
            adapter.propose_slidespec_repair(
                {"semanticId": "sem:title"},
                [{"code": "dom.clipping", "semanticId": "sem:title"}],
                **kwargs,
            )
        )
        for _ in range(3)
    ]
    assert calls == 2
    assert all(result.no_candidate_reason == "llm_provider_error" for result in results)
    assert results[-1].circuit_open_count == 1


def test_schema_rejections_trip_contract_health_circuit_across_inputs(monkeypatch) -> None:
    adapter.DEFAULT_REPAIR_LLM_NEGATIVE_CACHE.clear()
    adapter.DEFAULT_REPAIR_LLM_CIRCUIT_BREAKER = RepairLlmCircuitBreaker(
        failure_threshold=2, cooldown_seconds=60
    )
    calls = 0

    async def invalid_generate(client, model, messages, **kwargs):
        nonlocal calls
        calls += 1
        return {"plans": [{"strategy": "minimal_change", "operations": []}]}

    monkeypatch.setattr(adapter, "get_llm_provider", lambda: "provider-schema")
    monkeypatch.setattr(adapter, "get_model", lambda: "model-schema")
    monkeypatch.setattr(adapter, "get_llm_config", lambda: object())
    monkeypatch.setattr(adapter, "get_client", lambda config: object())
    monkeypatch.setattr(adapter, "_generate_once", invalid_generate)

    results = []
    for index in range(3):
        results.append(
            asyncio.run(
                adapter.propose_slidespec_repair(
                    {"semanticId": f"sem:{index}"},
                    [{"code": "clipping", "semanticId": f"sem:{index}"}],
                    slide_id=f"slide-{index}",
                    base_spec_revision=1,
                    base_spec_hash="sha256:" + str(index + 1) * 64,
                    expected_content_digest="sha256:" + "9" * 64,
                )
            )
        )

    assert calls == 2
    assert [result.no_candidate_reason for result in results[:2]] == [
        "llm_schema_rejected",
        "llm_schema_rejected",
    ]
    assert results[2].circuit_open_count == 1


def test_closed_repair_plan_rejects_page_regeneration_payload() -> None:
    with pytest.raises(ValueError, match="proposal.operation_not_allowed"):
        adapter.validate_proposal(
            {
                "plans": [
                    {
                        "planId": "bad",
                        "slideId": "slide-1",
                        "baseSpecRevision": 1,
                        "baseSpecHash": "sha256:" + "1" * 64,
                        "expectedContentDigest": "sha256:" + "2" * 64,
                        "strategy": "layout_reflow",
                        "operations": [
                            {"op": "regeneratePage", "target": "sem:title"}
                        ],
                    }
                ]
            },
            semantic_ids={"sem:title"},
            slide_id="slide-1",
            base_spec_revision=1,
            base_spec_hash="sha256:" + "1" * 64,
            expected_content_digest="sha256:" + "2" * 64,
        )
