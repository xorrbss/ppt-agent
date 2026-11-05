"""SVG review reads spend only their sealed slots, including after interruption."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import provider_usage_instrumentation, svg_authoring_profile as profiles
from services.generation_core.drivers.svg import quality
from services.generation_core.quality.vision_advisory_prompt import DESIGN_LOCAL_AXES
from services.svg_final_selected_continuation import (
    INITIAL_NAMESPACE, FinalSelectedEvidenceError, final_review_control,
)
from services.svg_source_local_review_continuation import _checked_current_local_group
from tests.unit.test_svg_page_error_class import _ReviewHarness
from utils.hashing import canonical_json_digest, sha256_hex
from utils.provider_overload import ProviderOutcomeError, ProviderQuotaPause


READ_EXECUTION_PLAN = profiles.read_quality_recovery_execution_plan


def _seal_snapshot(snapshot):
    snapshot["digest"] = canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True)


def _harness(monkeypatch, count):
    harness = _ReviewHarness(monkeypatch, count, version=2)
    # These replay tests pin the admitted v3 retry contract.
    harness.snapshot["quality_recovery_plan"] = profiles._read_only_review_plan(count)
    _seal_snapshot(harness.snapshot)
    harness.plan = READ_EXECUTION_PLAN(harness.snapshot)
    monkeypatch.setattr(profiles, "read_quality_recovery_execution_plan", READ_EXECUTION_PLAN)
    original_context = quality._design_context

    def design_context(*args, **kwargs):
        context = original_context(*args, **kwargs)
        context["snapshotDigest"] = quality.content_digest(harness.snapshot)
        return context

    monkeypatch.setattr(quality, "_design_context", design_context)
    return harness


def _provider(harness):
    provider = harness.provider()

    async def reviewed(*args, **kwargs):
        result = await provider(*args, **kwargs)
        for page in result["designReview"]["pages"]:
            page["designTargets"] = []
        return result

    return reviewed


def _run(harness, provider, **kwargs):
    return asyncio.run(quality.deck_advisory(harness.ctx, harness.pages, job=harness.job,
        read_png=lambda _: harness.pages[0][1], run_advisory=provider, **kwargs))


@pytest.mark.parametrize("count", [1, 2, 5, 6, 8, 9, 12, 80])
def test_admission_seals_one_read_retry_for_each_group_and_final_pass(count):
    request = SimpleNamespace(vision_qa=False)
    plan = profiles.seal_new_quality_recovery_plan(request, count)
    snapshot = dict(authoring_profile=profiles.ART_DIRECTION_PROFILE_V8,
                    quality_recovery_policy=profiles.QUALITY_RECOVERY_POLICY_V2, quality_recovery_plan=plan)
    _seal_snapshot(snapshot)
    execution = READ_EXECUTION_PLAN(snapshot)
    # v5: one full review per round up to the page limit, else local groups plus one global call.
    groups = 1 if count <= profiles.FULL_REVIEW_PAGE_LIMIT else (count + 4) // 5 + 1
    assert plan["schema"] == "svg-quality-recovery-plan/v5"
    assert plan["full_review_page_limit"] == profiles.FULL_REVIEW_PAGE_LIMIT == 8
    assert plan["review_contract_version"] == profiles.REVIEW_CONTRACT_VERSION
    assert plan["review_retry_limit"] == 1
    assert plan["review_call_limit"] == groups * 2 * (1 if count == 1 else 2)
    assert execution["phase_group_limit"] == groups * 2
    assert profiles.global_format_retry_control(snapshot) is None
    final = execution.get("final_selected_review")
    if count > 1:
        assert final["physical_offset"] == plan["review_call_limit"]
        assert final["review_call_limit"] == ((count + 4) // 5 + 1) * 2
        assert final["global_format_retry_limit"] == 0
    assert profiles.svg_quality_review_admission_budget(request, count) == (
        plan["review_call_limit"] + (final["review_call_limit"] if final else 0))
    request._svg_style_snapshot = deepcopy(snapshot)
    assert profiles.seal_new_quality_recovery_plan(request, count) == plan


@pytest.mark.parametrize("scope,count", [("full", 5), ("local", 7), ("global", 7)])
@pytest.mark.parametrize("kind", ["http500", "unknown", "schema"])
def test_one_failed_read_retries_same_input_and_keeps_superseded_evidence(monkeypatch, scope, count, kind):
    harness = _harness(monkeypatch, count)
    reviewed = _provider(harness)
    calls = []

    async def once(renders, **kwargs):
        context = kwargs["design_context"]
        calls.append((context["reviewScope"], deepcopy(context), renders,
                      provider_usage_instrumentation._ACTIVE_SCOPE.get().round_ordinal))
        if context["reviewScope"] == scope and sum(call[0] == scope for call in calls) == 1:
            if kind == "schema":
                return dict(skipped="design_review_schema_or_coverage_invalid",
                            schemaRejectionReason="index_coverage", parsedPageIndexCoverage=False)
            raise ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False,
                diagnostics={"httpStatus": 500} if kind == "http500" else {})
        return await reviewed(renders, **kwargs)

    result = _run(harness, once)
    assert result["vision"]["designGate"]["status"] == "passed"
    retried = next(marker for marker in harness.groups.values() if marker["superseded_attempts"])
    assert retried["review_attempt"] == 1 and len(retried["superseded_attempts"]) == 1
    prior = retried["superseded_attempts"][0]
    assert prior["physical_ordinal"] + 1 == retried["physical_ordinal"]
    assert prior["input_digest"] == retried["input_digest"]
    first_context = next(call[1] for call in calls if call[0] == scope)
    attempts = [call for call in calls if call[0] == scope
                and call[1].get("globalPageIndices") == first_context.get("globalPageIndices")]
    assert len(attempts) == 2
    if scope == "global" and kind == "schema":
        assert attempts[1][1] == {**attempts[0][1], "formatRetry": {
            "attempt": 1, "reason": "index_coverage", "fields": None}}
        assert attempts[0][2] == attempts[1][2]
    else:
        assert attempts[0][1:3] == attempts[1][1:3]
    assert attempts[1][3] == attempts[0][3] + 1
    if kind == "schema":
        assert prior["advisory"]["vision"]["schemaRejectionReason"] == "index_coverage"
    else:
        assert prior["failureCode"] == "public_provider_result_unknown"
        if kind == "http500":
            assert prior["failureDiagnostics"]["httpStatus"] == 500


@pytest.mark.parametrize("count", [1, 5])
def test_full_review_schema_rejection_has_the_same_single_retry(monkeypatch, count):
    harness = _harness(monkeypatch, count)
    provider = AsyncMock(return_value=dict(skipped="design_review_schema_or_coverage_invalid",
                                          schemaRejectionReason="full_composition_coverage"))
    reviewed = _provider(harness)

    async def schema_then_success(*args, **kwargs):
        if provider.await_count == 0:
            return await provider(*args, **kwargs)
        return await reviewed(*args, **kwargs)

    result = _run(harness, schema_then_success)
    assert result["vision"]["designGate"]["status"] == "passed"
    assert harness.groups["0"]["review_attempt"] == 1


@pytest.mark.parametrize("status", ["review_unknown", "review_inflight", "review_failed"])
def test_resume_dispatches_only_the_remaining_read_slot(monkeypatch, status):
    harness = _harness(monkeypatch, 5)
    _run(harness, _provider(harness))
    marker = harness.groups["0"]
    marker["status"] = status
    marker.pop("advisory")
    before = deepcopy(marker)
    provider = AsyncMock(side_effect=_provider(harness))
    result = _run(harness, provider)
    assert provider.await_count == 1
    assert result["vision"]["designGate"]["status"] == "passed"
    assert harness.groups["0"]["superseded_attempts"] == [
        {key: value for key, value in before.items() if key != "superseded_attempts"}]


@pytest.mark.parametrize("kind", ["provider", "schema"])
def test_exhausted_budget_stays_unreviewed_and_resume_spends_nothing(monkeypatch, kind):
    harness = _harness(monkeypatch, 5)
    provider = AsyncMock(side_effect=ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False))
    if kind == "schema":
        provider = AsyncMock(return_value={"skipped": "design_review_schema_or_coverage_invalid"})
    first = _run(harness, provider)
    assert provider.await_count == 2
    saved = deepcopy(harness.groups)
    second = _run(harness, provider)
    assert provider.await_count == 2 and harness.groups == saved
    assert first["vision"]["designGate"]["status"] == second["vision"]["designGate"]["status"] == "unreviewed"
    publication = quality.apply_quality_recovery_publication(harness.snapshot, harness.pages, second, job=harness.job)
    assert publication["status"] == "unavailable"
    assert all(quality.REVIEW_FLAG_KEY not in payload for payload, _ in harness.pages)


def test_overload_backs_off_without_holding_a_provider_permit(monkeypatch):
    harness = _harness(monkeypatch, 5)
    harness.ctx.semaphore = asyncio.Semaphore(1)
    delays = []

    async def backoff(seconds):
        assert not harness.ctx.semaphore.locked()
        delays.append(seconds)

    monkeypatch.setattr(quality.asyncio, "sleep", backoff)
    reviewed = _provider(harness)
    calls = 0

    async def overloaded(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False,
                overload=True, cooldown_seconds=60, diagnostics={"httpStatus": 503})
        return await reviewed(*args, **kwargs)

    assert _run(harness, overloaded)["vision"]["designGate"]["status"] == "passed"
    assert delays == [60]


def test_provider_and_schema_failures_share_one_budget(monkeypatch):
    harness = _harness(monkeypatch, 5)
    provider = AsyncMock(side_effect=[
        ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False),
        {"skipped": "design_review_schema_or_coverage_invalid"},
    ])
    result = _run(harness, provider)
    assert result["vision"]["designGate"]["status"] == "unreviewed"
    assert provider.await_count == 2
    assert harness.groups["0"]["review_attempt"] == 1
    _run(harness, provider)
    assert provider.await_count == 2


def test_unknown_second_attempt_cannot_be_dispatched_a_third_time(monkeypatch):
    harness = _harness(monkeypatch, 5)
    provider = AsyncMock(side_effect=asyncio.CancelledError())
    for attempt in range(2):
        with pytest.raises(asyncio.CancelledError):
            _run(harness, provider)
        assert harness.groups["0"]["review_attempt"] == attempt
    result = _run(harness, provider)
    assert provider.await_count == 2
    assert result["vision"]["designGate"]["status"] == "unreviewed"


@pytest.mark.parametrize("error", [asyncio.CancelledError(), ProviderQuotaPause(reset_at=10, observed_at=0),
                                   ValueError("svg_quality_recovery_authority_mismatch")])
def test_cancellation_quota_deferral_and_integrity_still_propagate(monkeypatch, error):
    harness = _harness(monkeypatch, 5)
    provider = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        _run(harness, provider)
    assert provider.await_count == 1 and harness.groups["0"]["status"] == "review_unknown"


@pytest.mark.parametrize("mutation", ["history", "attempt", "ordinal", "input"])
def test_resume_cannot_bypass_the_sealed_attempt_budget(monkeypatch, mutation):
    harness = _harness(monkeypatch, 5)
    _run(harness, _provider(harness))
    marker = harness.groups["0"]
    marker["status"] = "review_unknown"
    if mutation == "history":
        marker["superseded_attempts"] = [{}]
    elif mutation == "attempt":
        marker["review_attempt"] = -1
    elif mutation == "ordinal":
        marker["physical_ordinal"] += 1
    else:
        marker["input_digest"] = None
    provider = AsyncMock()
    with pytest.raises(ValueError, match="svg_quality_recovery_review_group_evidence_invalid"):
        _run(harness, provider)
    provider.assert_not_awaited()


@pytest.mark.parametrize("count", [1, 5, 7])
def test_physical_ordinals_do_not_overlap_rounds_or_final_review(monkeypatch, count):
    harness = _harness(monkeypatch, count)
    seen = []

    async def rejected(*args, **kwargs):
        seen.append(provider_usage_instrumentation._ACTIVE_SCOPE.get().round_ordinal)
        return {"skipped": "design_review_schema_or_coverage_invalid"}

    _run(harness, rejected)
    for phase in range(1, harness.plan["review_round_limit"]):
        harness.groups.clear()
        _run(harness, rejected, round_ordinal=phase)
    if count > 1:
        harness.groups.clear()
        _run(harness, rejected, final_selected=True)
    assert len(seen) == len(set(seen))
    request = SimpleNamespace(_svg_style_snapshot=harness.snapshot)
    assert max(seen) < profiles.svg_quality_review_admission_budget(request, count)


@pytest.mark.parametrize("version,count", [(1, 1), (1, 5), (1, 7), (2, 1), (2, 5), (2, 7)])
def test_old_sealed_plans_keep_unknown_and_schema_behavior(monkeypatch, version, count):
    harness = _ReviewHarness(monkeypatch, count, version=version)
    rounds = 1 if count == 1 else 2
    calls = rounds * ((count + 4) // 5 + (count > 5))
    old = dict(schema="svg-quality-recovery-plan/v2" if count == 1 else "svg-quality-recovery-plan/v1",
               review_round_limit=rounds, page_attempt_limit=3, slide_count=count,
               review_group_size=5, review_call_limit=calls)
    if version == 2 and count > 1:
        old["final_selected_review_control"] = final_review_control(
            count, physical_offset=calls + (rounds if count > 5 else 0))
    harness.snapshot["quality_recovery_plan"] = old
    _seal_snapshot(harness.snapshot)
    before = deepcopy(harness.snapshot)
    monkeypatch.setattr(profiles, "read_quality_recovery_execution_plan", READ_EXECUTION_PLAN)
    provider = AsyncMock(side_effect=ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False))
    _run(harness, provider)
    count_before = provider.await_count
    assert count_before == (count + 4) // 5
    _run(harness, provider)
    assert provider.await_count == count_before and harness.snapshot == before
    harness.groups.clear()
    rejected = AsyncMock(return_value={"skipped": "design_review_schema_or_coverage_invalid"})
    _run(harness, rejected)
    assert rejected.await_count == (count + 4) // 5
    request = SimpleNamespace(_svg_style_snapshot=deepcopy(harness.snapshot))
    assert profiles.seal_new_quality_recovery_plan(request, count) == old


@pytest.mark.parametrize("field,value", [("review_retry_limit", True), ("review_call_limit", 999),
                                         ("review_round_limit", 3)])
def test_plan_rejects_changed_or_unsealed_retry_budgets(field, value):
    request = SimpleNamespace()
    plan = profiles.seal_new_quality_recovery_plan(request, 5)
    plan[field] = value
    snapshot = dict(authoring_profile=profiles.ART_DIRECTION_PROFILE_V8,
                    quality_recovery_policy=profiles.QUALITY_RECOVERY_POLICY_V2, quality_recovery_plan=plan)
    snapshot["digest"] = canonical_json_digest(snapshot, allow_nan=True)
    with pytest.raises(ValueError, match="svg_quality_recovery_plan_invalid"):
        READ_EXECUTION_PLAN(snapshot)


@pytest.mark.parametrize("version,attempt", [(1, 0), (2, 0), (3, 0), (3, 1)])
def test_source_local_verifier_uses_the_producer_plan_ordinal_mapping(monkeypatch, version, attempt):
    count = 1 if version == 2 else 7
    ordinal = 0 if version == 2 else 1
    harness = _harness(monkeypatch, count)
    reviewed = _provider(harness)
    failed = False

    async def retry_local(*args, **kwargs):
        nonlocal failed
        if attempt and not failed and kwargs["design_context"].get("globalPageIndices") == [5, 6]:
            failed = True
            raise ProviderOutcomeError("public_provider_result_unknown", recovery_allowed=False)
        return await reviewed(*args, **kwargs)

    _run(harness, retry_local)
    marker = deepcopy(harness.groups[str(ordinal)])
    marker.update(namespace=INITIAL_NAMESPACE, physical_purpose="initial-continuation", review_scope="local")
    record = marker["advisory"]
    record["reviewScope"] = "local"
    for page in record["vision"]["designReview"]["pages"]:
        page["axes"] = {name: page["axes"][name] for name in DESIGN_LOCAL_AXES}
    record["digest"] = quality.content_digest({key: value for key, value in record.items() if key != "digest"})
    if version < 3:
        rounds = 1 if count == 1 else 2
        calls = rounds * ((count + 4) // 5 + (count > 5))
        plan = dict(schema=f"svg-quality-recovery-plan/v{version}", review_round_limit=rounds,
                    page_attempt_limit=3, slide_count=count, review_group_size=5, review_call_limit=calls)
        if count > 1:
            plan["final_selected_review_control"] = final_review_control(count, physical_offset=calls + rounds)
        harness.snapshot["quality_recovery_plan"] = plan
        _seal_snapshot(harness.snapshot)
        marker["physical_ordinal"] = ordinal
        marker.pop("review_attempt")
        marker.pop("superseded_attempts")
    else:
        assert marker["physical_ordinal"] == profiles.review_physical_ordinal(
            ordinal, phase_group_limit=harness.plan["phase_group_limit"], review_retry_limit=1,
            review_attempt=attempt)
    vector = [dict(png_digest=sha256_hex(png)) for _, png in harness.pages]
    assert _checked_current_local_group(marker, ordinal, vector, snapshot=harness.snapshot) == marker
    marker["physical_ordinal"] += 1
    with pytest.raises(FinalSelectedEvidenceError):
        _checked_current_local_group(marker, ordinal, vector, snapshot=harness.snapshot)
