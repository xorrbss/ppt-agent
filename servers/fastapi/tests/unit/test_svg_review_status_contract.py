"""Review continuation and dispatch accounting share the durable marker vocabulary."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import provider_usage_instrumentation as usage
from services import svg_source_local_review_continuation as continuation
from services.generation_core.quality.advisory_record import content_digest
from services.generation_core.quality.vision_advisory_prompt import DESIGN_PRINCIPLES_VERSION
from services.provider_pricing import ProviderModelPricing
from services.svg_cached_author_render_recovery import digest
from tests.unit.test_svg_page_error_class import _RecoveryHarness
from tests.unit.test_svg_review_read_retries import _harness, _provider, _run
from utils.hashing import sha256_hex


@pytest.mark.parametrize("status", ["review_unknown", "review_not_dispatched"])
def test_recovery_reopens_both_interrupted_round_statuses(monkeypatch, tmp_path, status):
    harness = _RecoveryHarness(monkeypatch, tmp_path, AsyncMock())
    harness.quality.quality_recovery_decisions = lambda *_args: {}
    harness.pages[0][0]["quality_recovery_execution"] = dict(
        schema="svg-quality-recovery-execution/v1", binding="binding",
        rounds=[dict(status=status, page_digests=[digest({"svg": p["svg_content"], "png": p["png_sha256"]})
                                                for p, _ in harness.pages])])
    harness.run()
    harness.quality.deck_advisory.assert_awaited_once()
    assert harness.pages[0][0]["quality_recovery_execution"]["rounds"][0]["status"] == "completed"
    harness.attempt_loop.assert_not_awaited()


@pytest.mark.parametrize("status", ["review_unknown", "review_not_dispatched"])
@pytest.mark.parametrize("round_status", ["review_unknown", "review_not_dispatched"])
def test_source_local_proof_and_archive_preserve_interrupted_global_groups(monkeypatch, status, round_status):
    harness = _harness(monkeypatch, 7)
    _run(harness, _provider(harness))
    harness.groups["2"].update(status=status, failureDiagnostics={})
    harness.groups["2"].pop("advisory")
    payloads = [payload for payload, _ in harness.pages]
    for payload in payloads:
        payload["source_digest"] = sha256_hex(payload["svg_content"].encode())
        payload["quality"].update(geometry_passed=True)
    execution = dict(schema="svg-quality-recovery-execution/v1", binding="1" * 64,
        reviewPrinciplesVersion=DESIGN_PRINCIPLES_VERSION,
        rounds=[dict(status=round_status, groups=deepcopy(harness.groups), page_digests=[
            digest({"svg": payload["svg_content"], "png": payload["png_sha256"]}) for payload in payloads])])
    payloads[0]["quality_recovery_execution"] = execution
    source_job = dict(id=str(harness.job.id), workspace_operation_id=str(harness.job.workspace_operation_id),
        presentation_id=str(harness.job.presentation_id), svg_style_snapshot=harness.snapshot, request_payload={})
    archive = {"job": source_job}
    working = {"checkpoints": [dict(slide_index=i, svg_checkpoint=payload) for i, payload in enumerate(payloads)]}
    before = deepcopy(execution)
    proof = continuation.build_source_local_proof(operation_id=source_job["workspace_operation_id"],
        job_id=source_job["id"], owner_id=str(harness.owner), tenant_id="tenant",
        archive_digest=digest(archive), parent_manifest_hash="4" * 64, payloads=payloads,
        execution=execution, source_job=source_job)
    assert proof is not None
    assert proof["source_local_group_digests"] == [content_digest(harness.groups[str(i)]) for i in range(2)]
    assert proof["preserved_unknown_global_digest"] == content_digest(harness.groups["2"])
    restored_job, restored = continuation.archived_source_local_payloads(archive, working, proof)
    assert restored_job == source_job and restored == payloads
    # Leaf consumers validate the original sealed vector through the same boundary.
    records, global_marker = continuation._source_local_records(execution, restored,
        sealed_vector=proof["source_pages"], snapshot=harness.snapshot)
    assert len(records) == 2 and global_marker["status"] == status
    assert execution == before


@pytest.mark.parametrize("reason", [None, "full_composition_coverage", "unknown_reason",
    "global_review_fields", "global_finding_fields", "global_group_fields", "axis_keys",
    "axis_semantics", "evidence_bounds", "index_coverage"])
def test_global_retry_feedback_uses_only_shared_format_reasons(monkeypatch, reason):
    harness = _harness(monkeypatch, 7)
    reviewed = _provider(harness)
    contexts = []

    async def provider(*args, **kwargs):
        context = kwargs["design_context"]
        if context["reviewScope"] == "global":
            contexts.append(deepcopy(context))
            if len(contexts) == 1:
                return dict(skipped="design_review_schema_or_coverage_invalid",
                    schemaRejectionReason=reason, schemaRejectionFields=["deckFlow"])
        return await reviewed(*args, **kwargs)

    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"
    assert len(contexts) == 2
    if reason in {None, "full_composition_coverage", "unknown_reason"}:
        assert "formatRetry" not in contexts[1]
    else:
        assert contexts[1]["formatRetry"] == dict(attempt=1, reason=reason, fields=["deckFlow"])


def test_local_reservation_barrier_is_durable_and_outside_provider_permits(monkeypatch):
    harness = _harness(monkeypatch, 12)
    checkpoint = harness.ctx.quality_recovery_review_group_checkpoint
    reviewed = _provider(harness)
    held = False
    lock = asyncio.Lock()

    class Permit:
        async def __aenter__(self):
            nonlocal held
            await lock.acquire()
            assert not held
            held = True

        async def __aexit__(self, *args):
            nonlocal held
            held = False
            lock.release()

    harness.ctx.semaphore = Permit()

    async def persist(ordinal, marker=None):
        assert held is (marker is not None and marker.get("status") == "review_inflight")
        await asyncio.sleep(0)
        return await checkpoint(ordinal, marker)

    async def provider(*args, **kwargs):
        assert held
        assert all(str(i) in harness.groups for i in range(3))
        return await reviewed(*args, **kwargs)

    harness.ctx.quality_recovery_review_group_checkpoint = persist
    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"


@pytest.mark.parametrize("reserved", [False, True])
@pytest.mark.parametrize("ledger", ["public", "durable"])
def test_ledger_reservation_consumes_a_review_slot_before_sdk(monkeypatch, reserved, ledger):
    harness = _harness(monkeypatch, 5)
    observations = []
    lease = SimpleNamespace()
    runtime = SimpleNamespace(control=SimpleNamespace(admit=AsyncMock(return_value=lease)))
    monkeypatch.setattr(usage, "_runtime_state", lambda: runtime)
    monkeypatch.setattr(usage, "ensure_runtime_job", AsyncMock(return_value=str(harness.job.id)))
    monkeypatch.setattr(usage, "_settle_provider_call", AsyncMock())
    monkeypatch.setattr(usage, "durable_provider_runtime_required", lambda: ledger == "durable")
    durable = AsyncMock(return_value=object() if ledger == "durable" else None)
    public = AsyncMock(return_value=SimpleNamespace(is_terminal_reuse=False, provider_idempotency_key="test"))
    if not reserved:
        (durable if ledger == "durable" else public).side_effect = asyncio.CancelledError()
    monkeypatch.setattr(usage, "reserve_provider_call_detached", durable)
    monkeypatch.setattr("services.public_operation_admission.reserve_public_provider_call_detached", public)
    monkeypatch.setattr(usage, "_assert_current_public_operation_fence",
                        AsyncMock(side_effect=asyncio.CancelledError()))
    pricing = ProviderModelPricing(revision="test", provider_id="unavailable", model_id="unavailable",
        input_microunits_per_million_tokens=1, output_microunits_per_million_tokens=1)

    async def stopped(*args, **kwargs):
        observation = usage._DISPATCH_OBSERVATION.get()
        observations.append(observation)
        with usage.public_operation_scope(harness.job.workspace_operation_id, attempt_token="test", fencing_token=1):
            async with usage.provider_call_admission(pricing=pricing):
                pytest.fail("the SDK must not start after a lost fence")

    with pytest.raises(asyncio.CancelledError):
        _run(harness, stopped)
    assert len(observations) == 1
    assert observations[0].admission_entered
    assert observations[0].ledger_reserved is reserved
    assert not observations[0].sdk_may_have_started
    marker = deepcopy(harness.groups["0"])
    assert marker["status"] == ("review_unknown" if reserved else "review_not_dispatched")
    assert _run(harness, _provider(harness))["vision"]["designGate"]["status"] == "passed"
    assert harness.groups["0"]["review_attempt"] == int(reserved)
    assert len(harness.groups["0"]["superseded_attempts"]) == int(reserved)
