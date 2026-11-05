"""Fresh SVG reviews own coverage and preserve independently verified axes."""
import json
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_authoring_profile as profiles
from services.generation_core.drivers.svg import quality
from services.generation_core.quality import vision_advisory as vision
from services.generation_core.quality import vision_advisory_prompt as prompt
from services.svg_source_local_review_continuation import _review_physical_ordinal
from tests.unit.test_svg_page_error_class import _publish_flags
from tests.unit.test_svg_page_quality_publication import PNG, _deck, _measured
from tests.unit.test_svg_review_read_retries import _harness, _provider, _run, _seal_snapshot


def _fresh(monkeypatch, count=7, plan=profiles._axis_review_plan):
    """A quality-first deck under ``plan``; the default pins the admitted v4 shape.

    v4 reviews a deck of more than five pages as local groups plus one global
    call, which these local/global tests exercise; stored v4 operations keep it.
    A fresh admission seals v5 (``profiles._full_review_plan``).
    """
    harness = _harness(monkeypatch, count)
    harness.snapshot["quality_recovery_plan"] = plan(count)
    _seal_snapshot(harness.snapshot)
    harness.plan = profiles.read_quality_recovery_execution_plan(harness.snapshot)
    original = quality._design_context

    def context(*args, **kwargs):
        result = original(*args, **kwargs)
        result["reviewContractVersion"] = profiles.REVIEW_CONTRACT_VERSION
        return result

    monkeypatch.setattr(quality, "_design_context", context)
    harness.ctx.quality_recovery_runtime_binding = "fixture-runtime"
    return harness


@pytest.mark.parametrize("failed_local", [False, True])
def test_missing_global_keeps_only_successful_local_groups_and_publication_flags(monkeypatch, failed_local):
    harness = _fresh(monkeypatch)
    reviewed = _provider(harness)
    calls = []

    async def provider(renders, **kwargs):
        context = kwargs["design_context"]
        calls.append(context)
        if (context["reviewScope"] == "global" or failed_local
                and context.get("globalPageIndices") == [5, 6]):
            return {"skipped": "design_review_schema_or_coverage_invalid",
                    "schemaRejectionReason": "server_partition_mismatch"}
        result = await reviewed(renders, **kwargs)
        page = result["designReview"]["pages"][0]
        if context["globalPageIndices"][0] == 0:
            page["axes"]["density_balance"].update(verdict="failed", severity="major", repairScope="design_rewrite")
        return result

    advisory = _run(harness, provider)
    gate = advisory["vision"]["designGate"]
    assert gate["schemaVersion"] == "svg-quality-first-design-gate/v2"
    assert gate["blockingPages"] == [0]
    assert gate["unreviewedPages"] == ([5, 6] if failed_local else [])
    assert gate["flowUnreviewedPages"] == list(range(7))
    assert gate["pages"][0]["axes"]["density_balance"]["verdict"] == "failed"
    assert all(page["evaluated"] for page in gate["pages"][:5])
    assert all(page["unreviewedAxes"] == ["repetition_deck_flow"] for page in gate["pages"][:5])
    decisions = quality.quality_recovery_decisions(harness.ctx, harness.pages, advisory)
    assert decisions[0]["target_axes"] == ["density_balance"]
    # Replaying the same source bytes does not purchase a coverage-format call.
    replay = _run(harness, AsyncMock(side_effect=AssertionError("sealed review dispatched again")))
    assert replay == advisory
    review, summary, flags = _publish_flags(harness, advisory)
    assert review["flowUnreviewedPages"] == list(range(7))
    assert summary["status"] == "review_required"
    assert flags[0] == "design:deck_flow_unreviewed,density_balance"
    assert flags[1] == "design:deck_flow_unreviewed"
    assert flags[6] == ("design:unreviewed" if failed_local else "design:deck_flow_unreviewed")
    assert len([call for call in calls if call["reviewScope"] == "global"]) == (0 if failed_local else 1)
    assert all("formatRetry" not in call for call in calls)


def test_invalid_full_review_does_not_manufacture_local_evidence(monkeypatch):
    harness = _fresh(monkeypatch, 3)
    advisory = _run(harness, AsyncMock(return_value={"skipped": "design_review_schema_or_coverage_invalid"}))
    gate = advisory["vision"]["designGate"]
    assert gate["unreviewedPages"] == [0, 1, 2]
    assert not any(page["evaluated"] for page in gate["pages"])
    assert all(not set(prompt.DESIGN_LOCAL_AXES) & set(page["axes"]) for page in gate["pages"])


@pytest.mark.parametrize("count,expected", [
    (6, [("full", list(range(6)))]),
    (8, [("full", list(range(8)))]),
    (9, [("local", [0, 1, 2, 3, 4]), ("local", [5, 6, 7, 8]), ("global", list(range(9)))]),
])
def test_fresh_plan_reviews_up_to_eight_pages_in_one_full_call(monkeypatch, count, expected):
    harness = _fresh(monkeypatch, count, lambda n: profiles.seal_new_quality_recovery_plan(SimpleNamespace(), n))
    assert harness.plan["schema"] == "svg-quality-recovery-plan/v5"
    reviewed = _provider(harness)
    calls = []

    async def provider(renders, **kwargs):
        context = kwargs["design_context"]
        indices = context.get("globalPageIndices", list(range(count)))  # a global call reviews the deck
        calls.append((context["reviewScope"], list(indices)))
        assert kwargs["slide_count"] == len(renders) == len(indices)
        return await reviewed(renders, **kwargs)

    advisory = _run(harness, provider)
    gate = advisory["vision"]["designGate"]
    # Local groups are dispatched concurrently; the global call comes after them.
    assert calls[-1] == expected[-1] and sorted(calls, key=lambda call: (call[0] == "global", call[1])) == expected
    assert gate["status"] == "passed" and gate["pageCoverageComplete"] is True
    assert gate["flowUnreviewedPages"] == [] and gate["unreviewedPages"] == []
    assert len(gate["reviewGroups"]) == len(expected)
    # A completed round is replayed from its sealed groups without a new call.
    assert _run(harness, AsyncMock(side_effect=AssertionError("sealed review dispatched again"))) == advisory


def test_v4_plans_keep_validating_with_their_local_and_global_shape():
    for count in (1, 5, 6, 7, 8, 12):
        plan = profiles._axis_review_plan(count)
        snapshot = dict(authoring_profile=profiles.ART_DIRECTION_PROFILE_V8,
                        quality_recovery_policy=profiles.QUALITY_RECOVERY_POLICY_V2, quality_recovery_plan=plan)
        _seal_snapshot(snapshot)
        assert profiles.read_quality_recovery_plan(snapshot) == plan
        groups = (count + 4) // 5 + (count > 5)
        assert profiles.read_quality_recovery_execution_plan(snapshot)["phase_group_limit"] == groups * 2
        assert "full_review_page_limit" not in plan
    # A v5 marker on v4 bytes (or a changed limit) is not an admitted plan.
    for forged in ({**profiles._axis_review_plan(7), "full_review_page_limit": 8},
                   {**profiles._full_review_plan(7), "full_review_page_limit": 12}):
        snapshot = dict(authoring_profile=profiles.ART_DIRECTION_PROFILE_V8,
                        quality_recovery_policy=profiles.QUALITY_RECOVERY_POLICY_V2, quality_recovery_plan=forged)
        _seal_snapshot(snapshot)
        with pytest.raises(ValueError, match="svg_quality_recovery_plan_invalid"):
            profiles.read_quality_recovery_plan(snapshot)


def test_one_unreviewed_flow_axis_does_not_invalidate_other_pages(monkeypatch):
    harness = _fresh(monkeypatch)
    reviewed = _provider(harness)

    async def provider(renders, **kwargs):
        result = await reviewed(renders, **kwargs)
        if kwargs["design_context"]["reviewScope"] == "global":
            page = result["designReview"]["pages"][6]
            page["evaluated"] = False
            page["axes"]["repetition_deck_flow"]["verdict"] = "unreviewed"
            result["designReview"]["deckFlow"]["evaluated"] = False
        return result

    advisory = _run(harness, provider)
    gate = advisory["vision"]["designGate"]
    assert gate["unreviewedPages"] == []
    assert gate["flowUnreviewedPages"] == [6]
    assert all(page["evaluated"] for page in gate["pages"])
    _, _, flags = _publish_flags(harness, advisory)
    assert flags == {**dict.fromkeys(range(6)), 6: "design:deck_flow_unreviewed"}


@pytest.mark.parametrize("plan", [profiles._axis_review_plan, profiles._full_review_plan])
@pytest.mark.parametrize("count,invalid", [(2, [0]), (5, [3]), (6, [5]), (7, [3]), (12, list(range(5)))])
@pytest.mark.parametrize("evidence", ["page", "png", "prior"])
def test_invalid_mandatory_evidence_only_excludes_its_pages(monkeypatch, count, invalid, evidence, plan):
    harness = _fresh(monkeypatch, count, plan)
    original = quality._design_context
    for index in invalid:
        harness.pages[index][0]["quality"]["passed"] = False
        harness.pages[index][0]["terminal"] = dict(index=index, reason="svg:svg_source_quality_failed",
                                                  remakeOutcome="verbatim_flag_only")
        if evidence == "png":
            harness.pages[index][0]["png_sha256"] = "0" * 64

    def context(*args, **kwargs):
        result = original(*args, **kwargs)
        result["examples"] = [dict(role="content", roles=["content"], digest="fixture-style-reference")]
        result["exampleDigests"] = ["fixture-style-reference"]
        for index in invalid:
            if evidence == "page":
                result["mandatoryEvidence"][index] = False
            elif evidence == "prior":
                result["priorCandidates"][index] = dict(png=PNG, mandatoryEvidenceValid=False)
        return result

    monkeypatch.setattr(quality, "_design_context", context)
    valid = [index for index in range(count) if index not in invalid]
    reviewed = _provider(harness)
    calls = []

    async def provider(renders, **kwargs):
        request = kwargs["design_context"]
        indices = request["globalPageIndices"]
        calls.append((request["reviewScope"], indices))
        assert not set(indices) & set(invalid)
        assert kwargs["slide_count"] == len(renders) == len(indices)
        assert len(request["mandatoryEvidence"]) == len(indices)
        assert all(request["mandatoryEvidence"])
        assert [page["deckIndex"] for page in request["pages"]] == indices
        assert not request["priorCandidates"]
        # Exercise the actual fixed-partition request/parser on the global subset.
        if request["reviewScope"] == "global":
            assert request["examples"] == request["exampleDigests"] == []
            parsed = vision._parse_baseline_review(_global_payload(request, len(indices)),
                                                  slide_count=len(indices), context=request)
            assert parsed is not None
        result = await reviewed(renders, **kwargs)
        result["designReview"]["deckFlow"]["repetitionGroups"] = [dict(pages=list(range(len(indices))))]
        return result

    before = deepcopy(harness.pages)
    advisory = _run(harness, provider)
    gate = advisory["vision"]["designGate"]
    assert harness.pages == before
    assert advisory["vision"]["reviewedPageIndices"] == valid
    assert advisory["vision"]["unreviewedPages"] == invalid
    assert gate["schemaVersion"] == "svg-quality-first-design-gate/v2"
    assert gate["unreviewedPages"] == gate["flowUnreviewedPages"] == invalid
    assert gate["reason"] == "mandatory_evidence_invalid"
    assert gate["status"] == "unreviewed" and not gate["pageCoverageComplete"]
    assert gate["blockingPages"] == []
    assert gate["deckFlow"]["evaluated"] is False
    assert gate["deckFlow"]["reason"] == "mandatory_evidence_invalid"
    assert gate["deckFlow"]["repetitionGroups"][0]["pages"] == valid
    assert {index for scope, indices in calls if scope != "global" for index in indices} == set(valid)
    if count > harness.plan.get("full_review_page_limit", 5):
        assert calls[-1] == ("global", valid)
    else:
        assert calls == [("full", valid)]
    for page in gate["pages"]:
        if page["index"] in invalid:
            assert page["reason"] == "mandatory_evidence_invalid"
            assert not page["evaluated"] and not page["mandatoryEvidenceValid"]
            assert page["unreviewedAxes"] == list(prompt.DESIGN_AXES)
        else:
            assert page["evaluated"] and page["status"] == "passed"
            assert page["unreviewedAxes"] == []
    assert quality.quality_recovery_decisions(harness.ctx, harness.pages, advisory) == {}
    assert _run(harness, AsyncMock(side_effect=AssertionError("sealed review dispatched again"))) == advisory
    if evidence == "png":
        # Review isolation does not weaken publication's original render-integrity fence.
        from services.authored_checkpoint_errors import AuthoredStageError
        with pytest.raises(AuthoredStageError) as raised:
            _publish_flags(harness, advisory)
        assert raised.value.code == "svg_quality_recovery_review_required"
        return
    review, summary, flags = _publish_flags(harness, advisory)
    assert review["status"] == "reviewed" and review["gateStatus"] == "unreviewed"
    assert review["unreviewedPages"] == review["flowUnreviewedPages"] == invalid
    assert review["flaggedPages"] == invalid
    assert summary["status"] == "review_required"
    assert flags == {index: "design:unreviewed" if index in invalid else None for index in range(count)}
    for index in invalid:
        assert harness.pages[index][0]["terminal"] == before[index][0]["terminal"]
        assert harness.pages[index][0]["quality"]["passed"] is False


@pytest.mark.parametrize("count", [1, 3, 7])
def test_all_mandatory_evidence_invalid_still_skips_review(monkeypatch, count):
    harness = _fresh(monkeypatch, count)
    original = quality._design_context

    def context(*args, **kwargs):
        return {**original(*args, **kwargs), "mandatoryEvidence": [False] * count}

    monkeypatch.setattr(quality, "_design_context", context)
    provider = AsyncMock(side_effect=AssertionError("invalid evidence dispatched"))
    advisory = _run(harness, provider)
    assert advisory["vision"] == {"skipped": "mandatory_evidence_invalid"}
    provider.assert_not_awaited()
    assert harness.groups == {}
    review, _, flags = _publish_flags(harness, advisory)
    assert review == {"status": "unavailable", "reason": "mandatory_evidence_invalid"}
    assert not any(flags.values())


def _global_context():
    return dict(policy=profiles.QUALITY_RECOVERY_POLICY_V2, reviewScope="global",
        reviewContractVersion=profiles.REVIEW_CONTRACT_VERSION, priorCandidates={},
        composition={"groups": [{"pages": [0, 2]}], "pages": [{}, {}, {}]}, pages=[], examples=[])


def _global_payload(context, count=3):
    axis = dict(verdict="passed", severity="none", repairScope="none", visibleEvidence="Clear progression.")
    return {"variantReview": {group: {key: dict(axis=deepcopy(axis), intentional=False, sourceEvidence="")
            for key in keys} for group, keys in prompt.server_review_partition(context, count).items()},
            "designTargets": []}


def test_request_carries_server_partition_and_parser_never_accepts_model_partitions():
    context = _global_context()
    partition = {"group-1": ["page-1", "page-3"], "group-2": ["page-2"]}
    assert prompt.server_review_partition(context, 3) == partition
    schema = prompt.baseline_review_json_schema(slide_count=3, context=context)
    wire = schema["properties"]["variantReview"]
    assert wire["required"] == list(partition)
    assert wire["properties"]["group-1"]["required"] == partition["group-1"]
    assert "findings" not in schema["properties"]
    messages = prompt.build_advisory_messages(b"sheet", slide_count=3, required=True,
        pngs=[b"png"] * 3, design_context=context)
    public = json.loads(messages[1].content[0].text)
    assert public["serverReviewPartition"] == partition
    assert "Partition every page explicitly" not in messages[0].content
    result = vision._parse_baseline_review(_global_payload(context), slide_count=3, context=context)
    assert [page["index"] for page in result["pages"]] == [0, 1, 2]
    assert result["deckFlow"]["evaluated"]


@pytest.mark.parametrize("mutation", ["omit_page", "add_page", "omit_group", "add_group", "invent_partition"])
def test_model_coverage_mutations_fail_only_the_global_review(mutation):
    context = _global_context()
    payload = _global_payload(context)
    raw = payload["variantReview"]
    if mutation == "omit_page":
        del raw["group-1"]["page-3"]
    elif mutation == "add_page":
        raw["group-1"]["page-9"] = deepcopy(raw["group-1"]["page-1"])
    elif mutation == "omit_group":
        del raw["group-2"]
    elif mutation == "add_group":
        raw["group-9"] = {}
    else:
        raw["cleanPages"] = [1, 2, 3]
    reasons = []
    assert vision._parse_baseline_review(payload, slide_count=3, context=context, rejection_reasons=reasons) is None
    assert reasons == ["server_partition_mismatch"]


def test_partial_global_answer_keeps_other_flow_verdicts():
    context = _global_context()
    payload = _global_payload(context)
    payload["variantReview"]["group-1"]["page-3"]["axis"]["verdict"] = "unreviewed"
    result = vision._parse_baseline_review(payload, slide_count=3, context=context)
    assert [page["evaluated"] for page in result["pages"]] == [True, True, False]
    assert not result["deckFlow"]["evaluated"]


def test_v3_plan_and_completed_gate_replay_unchanged(monkeypatch):
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=1))
    harness = _harness(monkeypatch, 7)
    before = deepcopy(harness.snapshot)
    admitted = SimpleNamespace(_svg_style_snapshot=before)
    assert profiles.seal_new_quality_recovery_plan(admitted, 7) == before["quality_recovery_plan"]
    assert profiles.read_quality_recovery_plan(before) == profiles._read_only_review_plan(7)
    provider = _provider(harness)

    async def missing_global(renders, **kwargs):
        if kwargs["design_context"]["reviewScope"] == "global":
            return {"skipped": "review_input_capacity_unavailable"}
        return await provider(renders, **kwargs)

    original = _run(harness, missing_global)
    # Captured by this fixture on the detached 075389fa4 base, including all group seals.
    assert quality.content_digest(before) == (
        "sha256:7749aa5270dbb058d9d7893f841c8e627cccf20ba554ada5efa31b388b040667")
    assert quality.content_digest(original) == (
        "sha256:5d67543d550b8ed87934fc08fa7639befc98c183fea7a3793b0aaf24b6c8d19a")
    gate = original["vision"]["designGate"]
    assert gate["schemaVersion"] == "svg-quality-first-design-gate/v1"
    assert gate["unreviewedPages"] == list(range(7))
    assert "flowUnreviewedPages" not in gate
    assert _run(harness, AsyncMock(side_effect=AssertionError("replay dispatched"))) == original
    assert harness.snapshot == before


@pytest.mark.parametrize("fresh", [False, True])
def test_continuation_uses_admitted_retry_physical_ordinal(fresh):
    # A source-local continuation reuses local groups, so a fresh (v5) plan
    # needs a deck past its full-review page limit to have a second group.
    plan = (profiles.seal_new_quality_recovery_plan(SimpleNamespace(), 12) if fresh
            else profiles._read_only_review_plan(7))
    snapshot = dict(authoring_profile=profiles.ART_DIRECTION_PROFILE_V8,
        quality_recovery_policy=profiles.QUALITY_RECOVERY_POLICY_V2, quality_recovery_plan=plan)
    _seal_snapshot(snapshot)
    assert _review_physical_ordinal({"review_attempt": 1}, 1, snapshot) == 3


def test_new_plan_marker_cannot_be_injected_into_a_v3_plan():
    plan = profiles._read_only_review_plan(7)
    plan["review_contract_version"] = profiles.REVIEW_CONTRACT_VERSION
    snapshot = dict(authoring_profile=profiles.ART_DIRECTION_PROFILE_V8,
        quality_recovery_policy=profiles.QUALITY_RECOVERY_POLICY_V2, quality_recovery_plan=plan)
    _seal_snapshot(snapshot)
    with pytest.raises(ValueError, match="svg_quality_recovery_plan_invalid"):
        profiles.read_quality_recovery_plan(snapshot)


def test_verified_local_repair_can_be_selected_without_claiming_flow_review(monkeypatch):
    deck = _deck(monkeypatch, count=1, version=2,
                 axes={0: {"density_balance": ("failed", "major", "design_rewrite")}})
    context = {**deck.context, "reviewContractVersion": profiles.REVIEW_CONTRACT_VERSION}
    before = deepcopy(deck.vision)
    before["promptVersion"] = prompt.AXIS_REVIEW_PROMPT_VERSION
    before["designReview"]["deckFlow"] = {"evaluated": False}
    del before["designReview"]["pages"][0]["axes"]["repetition_deck_flow"]
    old_gate = quality._seal_design_gate(context, deck.pages, [PNG], before)
    before["designGate"] = old_gate
    prior_advisory = quality.advisory_record(before, old_gate["pageDigests"])
    prior = deck.pages[0][0]
    new = deepcopy(prior)
    new["svg_content"] += "<!-- local repair -->"
    new["quality"] = _measured(new["svg_content"])
    context["priorCandidates"] = {0: dict(pageDigest=old_gate["pageDigests"][0],
        pngDigest=old_gate["pngDigests"][0], inputDigest=prior["input_digest"],
        mandatoryEvidenceValid=True, targetAxes=["density_balance"], targetGoals=[])}
    after = deepcopy(before)
    after["designReview"]["pages"][0]["axes"]["density_balance"].update(
        verdict="passed", severity="none", repairScope="none")
    after["designReview"]["candidateComparisons"] = [dict(index=0, evaluated=True,
        priorAxes=before["designReview"]["pages"][0]["axes"], visibleEvidence="Density improved.",
        axisChanges={name: dict(change="improved" if name == "density_balance" else "equivalent",
                               visibleEvidence="Source hierarchy preserved.") for name in prompt.DESIGN_LOCAL_AXES},
        blindedPresentation=dict(candidateLabel="A", priorLabel="B", displayOrder=["A", "B"],
                                 chronologyExposed=False, versionExposed=False))]
    new_gate = quality._seal_design_gate(context, [(new, PNG)], [PNG], after)
    after["designGate"] = new_gate
    new_advisory = quality.advisory_record(after, new_gate["pageDigests"])
    ctx = SimpleNamespace(snapshot=deck.snapshot, quality_recovery_runtime_binding="fixture-runtime")
    result = quality.compare_quality_recovery_candidates(ctx, prior, new, prior_advisory, new_advisory, 0)
    assert result["selected"] == "candidate"
    assert new_gate["flowUnreviewedPages"] == [0]
    assert new_gate["pages"][0]["axes"]["repetition_deck_flow"]["verdict"] == "unreviewed"
