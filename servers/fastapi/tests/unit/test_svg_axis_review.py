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


def _fresh(monkeypatch, count=7):
    harness = _harness(monkeypatch, count)
    harness.snapshot["quality_recovery_plan"] = profiles.seal_new_quality_recovery_plan(SimpleNamespace(), count)
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


def _global_context():
    return dict(policy=profiles.QUALITY_RECOVERY_POLICY_V2, reviewScope="global",
        reviewContractVersion=profiles.REVIEW_CONTRACT_VERSION, priorCandidates={},
        composition={"groups": [{"pages": [0, 2]}], "pages": [{}, {}, {}]}, pages=[], examples=[])


def _global_payload(context):
    axis = dict(verdict="passed", severity="none", repairScope="none", visibleEvidence="Clear progression.")
    return {"variantReview": {group: {key: dict(axis=deepcopy(axis), intentional=False, sourceEvidence="")
            for key in keys} for group, keys in prompt.server_review_partition(context, 3).items()},
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
    plan = (profiles.seal_new_quality_recovery_plan(SimpleNamespace(), 7) if fresh
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
