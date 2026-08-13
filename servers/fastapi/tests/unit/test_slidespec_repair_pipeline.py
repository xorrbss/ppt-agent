import asyncio
from time import perf_counter
import uuid

from models.slidespec_v1 import REPAIR_PLAN_SCHEMA, RepairPlan
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    PublicationReceipt,
    RepairConflictFootprint,
    RepairIterationGuard,
    RenderedCandidate,
    SensorResult,
    SlideSpecInput,
    SlideSpecRepairDependencies,
    SlideSpecRepairPipeline,
    canonical_ocr_inventory,
)
from services.slidespec_rollout_policy import resolve_slidespec_repair_rollout


def _rollout(
    monkeypatch, *, shadow=False, apply=True, index=10, all_slides=False
):
    monkeypatch.setenv("SLIDESPEC_REPAIR_SHADOW", "1" if shadow else "0")
    monkeypatch.setenv("SLIDESPEC_REPAIR_CANARY", "100")
    monkeypatch.setenv("SLIDESPEC_REPAIR_APPLY", "1" if apply else "0")
    monkeypatch.setenv(
        "SLIDESPEC_REPAIR_ALL_SLIDES", "1" if all_slides else "0"
    )
    return resolve_slidespec_repair_rollout(
        uuid.UUID("00000000-0000-0000-0000-000000000011"),
        slide_index=index,
        opt_in=True,
        owner_id="owner",
    )


def _spec():
    return SlideSpecInput(
        revision_id="17",
        spec_hash="sha256:" + "1" * 64,
        semantic_text={"sem:title": "5개월 조기완료 전략"},
        important_tokens=("5개월", "조기완료"),
    )


def _plan(
    plan_id="det-1",
    *,
    source="deterministic",
    operation=None,
    strategy=None,
    diagnostic_refs=(),
):
    operation = operation or {
        "op": "setSpacing",
        "target": "sem:title",
        "gap": 4,
    }
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": plan_id,
            "slideId": "slide-11",
            "baseSpecRevision": 17,
            "baseSpecHash": "sha256:" + "1" * 64,
            "expectedContentDigest": "sha256:" + "2" * 64,
            "scope": [operation["target"]],
            "diagnosticRefs": list(diagnostic_refs),
            "strategy": strategy
            or (
                "deterministic_cascade"
                if source == "deterministic"
                else "minimal_change"
            ),
            "operations": [operation],
            "limits": {
                "maxOperations": 4,
                "maxAffectedBlocks": 2,
                "maxLayoutDepth": 8,
            },
            "proposedBy": {
                "kind": "rule_engine" if source == "deterministic" else "llm",
                "policyVersion": "test/1",
            },
        }
    )


def _baseline():
    return DomEvidence((DomDefect("dom.clipping", "high", "sem:title"),))


def _dependencies(events, *, candidate_dom=None, proposer=None):
    async def cascade(spec, dom):
        events.append("cascade")
        return [_plan()]

    async def validate(spec, plan):
        events.append("validate")
        return SensorResult(True)

    async def render(spec, plan):
        events.append("render")
        return RenderedCandidate("sha256:html", "sha256:render")

    async def inspect(artifact):
        events.append("dom")
        return candidate_dom or DomEvidence()

    async def ocr(artifact, inventory):
        events.append(("ocr", inventory))
        return SensorResult(True)

    async def vqa(artifact):
        events.append("vqa")
        return SensorResult(True, quality_gain=0.50)

    async def publish(spec, plan, artifact):
        events.append("publish")
        return PublicationReceipt(True, True, True, "receipt-1")

    return SlideSpecRepairDependencies(
        deterministic_cascade=cascade,
        validate_plan=validate,
        render_plan=render,
        inspect_dom=inspect,
        run_ocr=ocr,
        run_vqa=vqa,
        publish=publish,
        propose=proposer,
    )


def test_defaults_to_all_slide_active_and_kill_switch_wins(monkeypatch):
    for name in (
        "SLIDESPEC_IMPORT_SHADOW",
        "SLIDESPEC_REPAIR_SHADOW",
        "SLIDESPEC_REPAIR_CANARY",
        "SLIDESPEC_REPAIR_APPLY",
        "SLIDESPEC_REPAIR_ALL_SLIDES",
        "SLIDESPEC_KILL_SWITCH",
    ):
        monkeypatch.delenv(name, raising=False)
    deck = uuid.uuid4()
    active = resolve_slidespec_repair_rollout(deck, slide_index=0, opt_in=True)
    assert active.route == "slidespec_active"
    assert active.publisher_allowed is True
    monkeypatch.setenv("SLIDESPEC_KILL_SWITCH", "1")
    killed = resolve_slidespec_repair_rollout(deck, slide_index=10, opt_in=True)
    assert killed.route == "legacy"
    assert killed.publisher_allowed is False


def test_canary_defaults_to_page_11_and_explicit_apply_only(monkeypatch):
    page_11 = _rollout(monkeypatch)
    assert page_11.publisher_allowed is True
    other_page = _rollout(monkeypatch, index=9)
    assert other_page.publisher_allowed is False
    assert other_page.route == "legacy"


def test_explicit_all_slides_rollout_admits_every_slide(monkeypatch):
    first_page = _rollout(monkeypatch, index=0, all_slides=True)
    later_page = _rollout(monkeypatch, index=37, all_slides=True)

    assert first_page.publisher_allowed is True
    assert first_page.route == "slidespec_active"
    assert later_page.publisher_allowed is True
    assert later_page.route == "slidespec_active"


def test_synthetic_page_11_applies_after_all_publication_receipt_checks(monkeypatch):
    events = []
    result = asyncio.run(
        SlideSpecRepairPipeline(_dependencies(events)).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )
    assert result.trigger == "geometry"
    assert result.outcomes[0].applied is True
    assert result.funnel.metrics() == {
        "candidate_proposed": 1,
        "candidate_rendered": 1,
        "validation_passed": 1,
        "candidate_accepted": 1,
        "validation_rejected": 0,
        "validation_inconclusive": 0,
        "apply_committed": 1,
        "publish_active": 1,
        "postverify_passed": 1,
        "applied": 1,
        "rolled_back": 0,
        "legacy_preserved": 0,
    }
    assert "publish" in events
    assert "proposal" not in events


def test_shadow_never_calls_publisher_and_ocr_truth_is_slidespec(monkeypatch):
    events = []
    result = asyncio.run(
        SlideSpecRepairPipeline(_dependencies(events)).run(
            spec=_spec(),
            baseline_dom=_baseline(),
            rollout=_rollout(monkeypatch, shadow=True, apply=False),
        )
    )
    assert result.outcomes[0].applied is False
    assert result.funnel.legacy_preserved == 1
    assert "publish" not in events
    inventory = next(
        item[1] for item in events if isinstance(item, tuple) and item[0] == "ocr"
    )
    assert inventory == canonical_ocr_inventory(_spec())


def test_dom_hard_failure_skips_expensive_sensors(monkeypatch):
    events = []
    bad_dom = DomEvidence((DomDefect("dom.clipping", "medium", "sem:title"),))
    result = asyncio.run(
        SlideSpecRepairPipeline(_dependencies(events, candidate_dom=bad_dom)).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )
    assert result.funnel.candidate_rejected == 1
    assert "publish" not in events and "vqa" not in events
    assert not any(
        isinstance(item, tuple) and item[0] == "ocr" for item in events
    )


def test_incomplete_dom_telemetry_preserves_missing_constraint_diagnostics(
    monkeypatch,
):
    events = []
    missing = DomEvidence(
        (
            DomDefect(
                "constraint.evidence.missing",
                "high",
                "sem:title",
                ("con:anchor-title",),
                qid="q-title",
                diagnostics={"observation": "offset_parent"},
            ),
        ),
        telemetry_complete=False,
    )
    result = asyncio.run(
        SlideSpecRepairPipeline(
            _dependencies(events, candidate_dom=missing)
        ).run(
            spec=_spec(),
            baseline_dom=_baseline(),
            rollout=_rollout(monkeypatch),
        )
    )

    outcome = result.outcomes[0]
    assert outcome.validation_status == "inconclusive"
    assert outcome.reason_codes == ("dom.telemetry_incomplete",)
    assert outcome.diagnostics["dom_telemetry"] == {
        "complete": False,
        "missing_evidence": [
            {
                "code": "constraint.evidence.missing",
                "severity": "high",
                "semantic_id": "sem:title",
                "qid": "q-title",
                "related_semantic_ids": ["con:anchor-title"],
                "diagnostics": {"observation": "offset_parent"},
            }
        ],
    }
    assert "publish" not in events and "vqa" not in events


def test_concrete_dom_failure_wins_over_unrelated_missing_evidence_and_tries_next(
    monkeypatch,
):
    events = []
    mixed = DomEvidence(
        (
            DomDefect("dom.clipping", "medium", "sem:title", qid="q-title"),
            DomDefect(
                "constraint.evidence.missing",
                "high",
                "sem:title",
                ("con:unobserved",),
            ),
        ),
        telemetry_complete=False,
    )
    dependencies = _dependencies(events, candidate_dom=mixed)

    async def cascade(spec, dom):
        return [
            _plan("first"),
            _plan(
                "second",
                operation={
                    "op": "setSpacing",
                    "target": "sem:title",
                    "gap": 8,
                },
            ),
        ]

    dependencies.deterministic_cascade = cascade
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(),
            baseline_dom=_baseline(),
            rollout=_rollout(monkeypatch),
        )
    )

    assert len(result.outcomes) == 2
    assert all(item.validation_status == "rejected" for item in result.outcomes)
    assert all(item.reason_codes == ("dom.clipping",) for item in result.outcomes)
    assert result.funnel.candidate_inconclusive == 0
    assert "vqa" not in events


def test_inconclusive_sensor_stops_without_trying_another_candidate(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return [
            _plan("cheap"),
            _plan(
                "next",
                operation={"op": "setSpacing", "target": "sem:title", "gap": 8},
            ),
        ]

    async def ocr(artifact, inventory):
        events.append("ocr-inconclusive")
        return SensorResult(False, "ocr.timeout", conclusive=False)

    dependencies.deterministic_cascade = cascade
    dependencies.run_ocr = ocr
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )
    assert len(result.outcomes) == 1
    assert result.outcomes[0].validation_status == "inconclusive"
    assert result.funnel.legacy_preserved == 1
    assert "publish" not in events


def test_missing_visual_gain_fails_closed_and_preserves_original(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def vqa_without_comparison_score(artifact):
        events.append("vqa-without-gain")
        return SensorResult(True)

    dependencies.run_vqa = vqa_without_comparison_score
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert result.outcomes[0].validation_status == "inconclusive"
    assert result.outcomes[0].reason_codes == ("vqa.improvement_unavailable",)
    assert result.funnel.applied == 0
    assert result.funnel.legacy_preserved == 1
    assert "publish" not in events


def test_zero_gain_without_resolved_issue_is_not_monotonic_and_cannot_publish(
    monkeypatch,
):
    events = []
    dependencies = _dependencies(events)

    async def neutral_vqa(artifact):
        events.append("vqa-neutral")
        return SensorResult(True, quality_gain=0.0)

    dependencies.run_vqa = neutral_vqa
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert result.outcomes[0].validation_status == "rejected"
    assert result.outcomes[0].reason_codes == ("vqa.non_monotonic",)
    assert result.outcomes[0].quality_gain == 0.0
    assert result.outcomes[0].applied is False
    assert result.funnel.publish_active == 0
    assert "publish" not in events


def test_conclusive_sensor_failure_tries_next_candidate_when_peer_is_unavailable(
    monkeypatch,
):
    events = []
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return [
            _plan("bad"),
            _plan(
                "good",
                operation={"op": "setSpacing", "target": "sem:title", "gap": 8},
            ),
        ]

    sensor_round = 0

    async def ocr(artifact, inventory):
        nonlocal sensor_round
        sensor_round += 1
        if sensor_round == 1:
            return SensorResult(False, "ocr.recall_regressed", conclusive=True)
        return SensorResult(True)

    async def vqa(artifact):
        if sensor_round == 1:
            return SensorResult(False, "vqa.unavailable", conclusive=False)
        return SensorResult(True, quality_gain=0.50)

    dependencies.deterministic_cascade = cascade
    dependencies.run_ocr = ocr
    dependencies.run_vqa = vqa
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert [outcome.plan_id for outcome in result.outcomes] == ["bad", "good"]
    assert result.outcomes[0].validation_status == "rejected"
    assert result.outcomes[0].reason_codes == ("ocr.recall_regressed",)
    assert result.outcomes[1].applied is True
    assert result.funnel.candidate_inconclusive == 0


def test_minimum_cost_candidate_is_evaluated_first(monkeypatch):
    events = []
    expensive = _plan(
        "expensive",
        operation={
            "op": "setBounds",
            "target": "sem:title",
            "bounds": {"x": 0, "y": 0.05, "width": 1, "height": 0.2},
        },
    )
    cheap = _plan("cheap")
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return [expensive, cheap]

    async def validate(spec, plan):
        events.append(("validated-plan", plan.plan_id))
        return SensorResult(True)

    dependencies.deterministic_cascade = cascade
    dependencies.validate_plan = validate
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )
    assert events[0] == ("validated-plan", "cheap")
    assert result.outcomes[0].plan_id == "cheap"
    assert result.outcomes[0].applied is True


def test_selects_largest_rendered_quality_gain_not_first_accepted(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    cheap = _plan("cheap")
    visibly_better = _plan(
        "visibly-better",
        operation={"op": "setSpacing", "target": "sem:title", "gap": 8},
    )

    async def cascade(spec, dom):
        return [cheap, visibly_better]

    async def render(spec, plan):
        return RenderedCandidate(
            f"html:{plan.plan_id}",
            f"render:{plan.plan_id}",
            (0, 1, 0.0, 0.01 if plan.plan_id == "cheap" else 0.02, 1, plan.plan_id),
        )

    async def vqa(artifact):
        gain = 0.40 if artifact.html_hash.endswith("cheap") else 0.75
        return SensorResult(True, quality_gain=gain)

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        return PublicationReceipt(True, True, True, "receipt-best")

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert ("published-plan", "visibly-better") in events
    assert result.outcomes[0].plan_id == "visibly-better"
    assert result.outcomes[0].selected is True
    assert result.outcomes[0].quality_gain == 0.75
    assert result.outcomes[1].reason_codes == ("candidate.not_selected",)
    assert result.funnel.candidate_accepted == 2
    assert result.telemetry.selected_quality_gain == 0.75


def test_equal_visual_gain_uses_realized_minimum_change_cost(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return [_plan("planned-cheap"), _plan("actually-cheap")]

    async def render(spec, plan):
        actual_delta = 0.20 if plan.plan_id == "planned-cheap" else 0.01
        return RenderedCandidate(
            f"html:{plan.plan_id}",
            f"render:{plan.plan_id}",
            (0, 1, 0.0, actual_delta, 1, plan.plan_id),
        )

    async def vqa(artifact):
        return SensorResult(True, quality_gain=0.60)

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        return PublicationReceipt(True, True, True, "receipt-cheapest")

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert ("published-plan", "actually-cheap") in events
    assert result.outcomes[0].plan_id == "actually-cheap"
    assert result.outcomes[0].selected is True


def test_maximum_repair_prefers_larger_measured_gain_when_safe_intervals_overlap(
    monkeypatch,
):
    events = []
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return [_plan("small-change"), _plan("uncertain-high-score")]

    async def render(spec, plan):
        actual_delta = 0.01 if plan.plan_id == "small-change" else 0.20
        return RenderedCandidate(
            f"html:{plan.plan_id}",
            f"render:{plan.plan_id}",
            (0, 1, 0.0, actual_delta, 1, plan.plan_id),
        )

    async def vqa(artifact):
        if artifact.html_hash.endswith("small-change"):
            return SensorResult(
                True,
                quality_gain=0.60,
                quality_gain_interval=(0.48, 0.72),
            )
        return SensorResult(
            True,
            quality_gain=0.68,
            quality_gain_interval=(0.45, 0.80),
        )

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        return PublicationReceipt(True, True, True, "receipt-uncertainty")

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert ("published-plan", "uncertain-high-score") in events
    assert result.outcomes[0].plan_id == "uncertain-high-score"
    assert result.outcomes[0].quality_gain_interval == (0.45, 0.80)
    assert result.telemetry.quality_gain_intervals == (
        (0.48, 0.72),
        (0.45, 0.80),
    )


def test_equal_visual_gain_minimizes_soft_constraint_cost_before_repair_cost(
    monkeypatch,
):
    events = []
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return [_plan("cheap-but-strained"), _plan("relationship-safe")]

    async def render(spec, plan):
        strained = plan.plan_id == "cheap-but-strained"
        return RenderedCandidate(
            f"html:{plan.plan_id}",
            f"render:{plan.plan_id}",
            (0, 1, 0.0, 0.01 if strained else 0.02, 1, plan.plan_id),
            2.5 if strained else 0,
        )

    async def vqa(artifact):
        return SensorResult(True, quality_gain=0.60)

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        return PublicationReceipt(True, True, True, "receipt-constraint-safe")

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert ("published-plan", "relationship-safe") in events
    assert result.outcomes[0].plan_id == "relationship-safe"
    assert result.outcomes[0].selected is True


def test_llm_is_one_call_and_at_most_two_candidates(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def cascade(spec, dom):
        return []

    async def proposer(spec, dom):
        events.append("proposal")
        return [
            _plan("llm-1", source="llm"),
            _plan("llm-2", source="llm"),
            _plan("llm-3", source="llm"),
        ]

    async def validate(spec, plan):
        events.append(("validated-plan", plan.plan_id))
        return SensorResult(False, "schema.invalid_value")

    dependencies.deterministic_cascade = cascade
    dependencies.propose = proposer
    dependencies.validate_plan = validate
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )
    assert events.count("proposal") == 1
    assert [event[1] for event in events if isinstance(event, tuple)] == [
        "llm-1",
        "llm-2",
    ]
    assert result.telemetry.llm_calls == 1
    assert result.telemetry.llm_candidates == 2


def test_pattern_fast_path_stops_before_full_local_candidate_search(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    learned = _plan(
        "rp-pattern",
        operation={"op": "setSpacing", "target": "sem:title", "gap": 8},
    )
    local = _plan(
        "rp-local",
        operation={"op": "setSpacing", "target": "sem:title", "gap": 12},
    )

    async def cascade(spec, dom):
        return [local, learned]

    async def validate(spec, plan):
        events.append(("validated-plan", plan.plan_id))
        return SensorResult(True)

    applied = []
    dependencies.deterministic_cascade = cascade
    dependencies.validate_plan = validate
    dependencies.candidate_partition = lambda plans: (
        tuple(plan for plan in plans if plan.plan_id == "rp-pattern"),
        tuple(plan for plan in plans if plan.plan_id != "rp-pattern"),
    )
    dependencies.applied_plan_observer = lambda plan: applied.append(plan.plan_id)

    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert ("validated-plan", "rp-pattern") in events
    assert ("validated-plan", "rp-local") not in events
    assert result.outcomes[0].plan_id == "rp-pattern"
    assert result.outcomes[0].applied is True
    assert applied == ["rp-pattern"]


def test_candidate_specific_publish_rejection_tries_next_validated_candidate(
    monkeypatch,
):
    events = []
    dependencies = _dependencies(events)
    first = _plan("largest-gain")
    second = _plan(
        "next-gain",
        operation={"op": "setSpacing", "target": "sem:title", "gap": 8},
    )

    async def cascade(spec, dom):
        return [first, second]

    async def render(spec, plan):
        return RenderedCandidate(
            f"html:{plan.plan_id}",
            f"render:{plan.plan_id}",
            (0, 1, 0.0, 0.01, 1, plan.plan_id),
        )

    async def vqa(artifact):
        gain = 0.8 if artifact.html_hash.endswith("largest-gain") else 0.6
        return SensorResult(True, quality_gain=gain)

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        if plan.plan_id == "largest-gain":
            return PublicationReceipt(
                False,
                False,
                False,
                reason_code="constraint.evidence.missing",
            )
        return PublicationReceipt(True, True, True, "receipt-next")

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "published-plan"
    ] == [
        ("published-plan", "largest-gain"),
        ("published-plan", "next-gain"),
    ]
    assert result.outcomes[0].plan_id == "largest-gain"
    assert result.outcomes[0].validation_status == "rejected"
    assert result.outcomes[0].reason_codes == ("constraint.evidence.missing",)
    assert result.outcomes[1].plan_id == "next-gain"
    assert result.outcomes[1].applied is True


def test_nonconflicting_plans_merge_into_one_composite_and_stop_lazily(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    title = _plan("title", operation={"op": "setSpacing", "target": "sem:title", "gap": 4})
    body = _plan("body", operation={"op": "setSpacing", "target": "sem:body", "gap": 6})

    async def cascade(spec, dom):
        return [title, body]

    async def render(spec, plan):
        events.append(("rendered-plan", plan.plan_id, len(plan.operations)))
        return RenderedCandidate(f"html:{plan.plan_id}", f"render:{plan.plan_id}")

    async def vqa(artifact):
        return SensorResult(
            True,
            quality_gain=0.8,
            resolved_issue_count=2,
            remaining_issue_count=0,
            quality_margin=0.2,
        )

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        return PublicationReceipt(True, True, True, "receipt-composite")

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    rendered = [event for event in events if isinstance(event, tuple) and event[0] == "rendered-plan"]
    assert len(rendered) == 1
    assert rendered[0][1].startswith("rp_composite_")
    assert rendered[0][2] == 2
    assert result.telemetry.composite_plan_count == 1
    assert result.telemetry.composite_operations_merged == 2
    assert result.telemetry.full_render_count == 1


def test_failed_composite_is_decomposed_to_individual_candidates(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    title = _plan("title", operation={"op": "setSpacing", "target": "sem:title", "gap": 4})
    body = _plan("body", operation={"op": "setSpacing", "target": "sem:body", "gap": 6})

    async def cascade(spec, dom):
        return [title, body]

    async def render(spec, plan):
        events.append(("rendered-plan", plan.plan_id))
        return RenderedCandidate(f"html:{plan.plan_id}", f"render:{plan.plan_id}")

    async def inspect(artifact):
        if artifact.html_hash.startswith("html:rp_composite_"):
            return DomEvidence((DomDefect("dom.unintended_overlap", "high", "sem:title"),))
        return DomEvidence()

    async def vqa(artifact):
        return SensorResult(
            True,
            quality_gain=0.5,
            resolved_issue_count=1,
            remaining_issue_count=0,
            quality_margin=0.2,
        )

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.inspect_dom = inspect
    dependencies.run_vqa = vqa
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    rendered_ids = [event[1] for event in events if isinstance(event, tuple) and event[0] == "rendered-plan"]
    assert rendered_ids[0].startswith("rp_composite_")
    assert rendered_ids[1] in {"title", "body"}
    assert result.outcomes[-1].applied is True
    assert result.telemetry.individual_fallback_count == 1


def test_shared_constraint_footprint_keeps_candidates_sequential(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    title = _plan("title", operation={"op": "setSpacing", "target": "sem:title", "gap": 4})
    body = _plan("body", operation={"op": "setSpacing", "target": "sem:body", "gap": 6})

    async def cascade(spec, dom):
        return [title, body]

    dependencies.deterministic_cascade = cascade
    dependencies.plan_footprint = lambda plan: RepairConflictFootprint(
        nodes=frozenset(plan.scope), constraints=frozenset({"shared-grid"})
    )
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert result.funnel.applied == 1
    assert result.telemetry.composite_plan_count == 0


def test_pattern_then_deterministic_then_bounded_llm_route(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    pattern = _plan("pattern", operation={"op": "setSpacing", "target": "sem:title", "gap": 4})
    local = _plan("local", operation={"op": "setSpacing", "target": "sem:body", "gap": 6})
    llm = _plan("llm", source="llm")

    async def cascade(spec, dom):
        return [local, pattern]

    async def validate(spec, plan):
        events.append(("validated-plan", plan.plan_id))
        if plan.plan_id in {"pattern", "local"}:
            return SensorResult(False, "constraint.infeasible")
        return SensorResult(True)

    async def propose(spec, dom):
        events.append("proposal")
        return [llm]

    dependencies.deterministic_cascade = cascade
    dependencies.validate_plan = validate
    dependencies.propose = propose
    dependencies.candidate_partition = lambda plans: (
        tuple(plan for plan in plans if plan.plan_id == "pattern"),
        tuple(plan for plan in plans if plan.plan_id != "pattern"),
    )
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert [event[1] for event in events if isinstance(event, tuple) and event[0] == "validated-plan"] == [
        "pattern",
        "local",
        "llm",
    ]
    assert result.outcomes[-1].plan_id == "llm"
    assert result.outcomes[-1].applied is True
    assert result.telemetry.llm_pattern_call_count == 1
    assert result.telemetry.individual_fallback_count == 1


def test_lazy_validation_expands_partial_result_but_skips_later_full_render(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    plans = [
        _plan("a", operation={"op": "setSpacing", "target": "sem:title", "gap": 4}),
        _plan("b", operation={"op": "setSpacing", "target": "sem:title", "gap": 8}),
        _plan("c", operation={"op": "setSpacing", "target": "sem:title", "gap": 12}),
    ]

    async def cascade(spec, dom):
        return plans

    async def render(spec, plan):
        events.append(("rendered-plan", plan.plan_id))
        return RenderedCandidate(f"html:{plan.plan_id}", f"render:{plan.plan_id}")

    async def vqa(artifact):
        plan_id = artifact.html_hash.rsplit(":", 1)[-1]
        if plan_id == "a":
            return SensorResult(
                True,
                quality_gain=0.3,
                resolved_issue_count=1,
                remaining_issue_count=1,
                quality_margin=0.2,
            )
        return SensorResult(
            True,
            quality_gain=0.7,
            resolved_issue_count=2,
            remaining_issue_count=0,
            quality_margin=0.2,
        )

    dependencies.deterministic_cascade = cascade
    dependencies.render_plan = render
    dependencies.run_vqa = vqa
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert [event[1] for event in events if isinstance(event, tuple) and event[0] == "rendered-plan"] == ["a", "b"]
    assert result.outcomes[0].plan_id == "b"
    assert result.outcomes[0].applied is True
    assert result.telemetry.full_render_count == 2


def test_cas_failure_stops_without_trying_validated_alternative(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    first = _plan("first")
    second = _plan("second", operation={"op": "setSpacing", "target": "sem:title", "gap": 8})

    async def cascade(spec, dom):
        return [first, second]

    async def publish(spec, plan, artifact):
        events.append(("published-plan", plan.plan_id))
        return PublicationReceipt(False, False, False, reason_code="artifact.publish.cas_conflict")

    dependencies.deterministic_cascade = cascade
    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "published-plan"
    ] == [("published-plan", "first")]
    assert result.funnel.applied == 0
    assert result.funnel.legacy_preserved == 1


def test_iteration_guard_blocks_repeat_oscillation_and_thirteenth_apply(monkeypatch):
    guard = RepairIterationGuard()
    first = _plan("first", operation={"op": "setSpacing", "target": "sem:title", "gap": 4})
    second = _plan("second", operation={"op": "setSpacing", "target": "sem:title", "gap": 8})
    guard.record_applied(first)
    assert guard.rejection_reason(first) == "repair.plan_signature_repeated"
    guard.record_applied(second)
    assert guard.rejection_reason(first) == "repair.oscillation_detected"

    capped = RepairIterationGuard()
    for index in range(12):
        capped.record_applied(
            _plan(
                f"applied-{index}",
                operation={"op": "setSpacing", "target": "sem:title", "gap": index},
            )
        )
    assert capped.rejection_reason(
        _plan("thirteenth", operation={"op": "setSpacing", "target": "sem:title", "gap": 20})
    ) == "repair.iteration_limit"


def test_pipeline_rejects_previously_applied_signature_before_render(monkeypatch):
    events = []
    dependencies = _dependencies(events)
    dependencies.iteration_guard.record_applied(_plan())

    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert "render" not in events
    assert result.outcomes[0].reason_codes == ("repair.plan_signature_repeated",)
    assert result.telemetry.repeated_plan_blocked == 1
    assert result.telemetry.render_candidates_rejected_early == 1


def test_negative_quality_gain_is_not_published(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def vqa(artifact):
        return SensorResult(True, quality_gain=-0.01)

    dependencies.run_vqa = vqa
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert "publish" not in events
    assert result.outcomes[0].reason_codes == ("vqa.non_monotonic",)
    assert result.funnel.applied == 0


def test_readback_mismatch_is_not_applied_and_records_rollback(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def publish(spec, plan, artifact):
        events.append("publish")
        return PublicationReceipt(
            True, True, False, "receipt-conflict", rolled_back=True
        )

    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )
    assert result.outcomes[0].applied is False
    assert result.outcomes[0].rolled_back is True
    assert result.funnel.applied == 0
    assert result.funnel.rolled_back == 1
    assert result.funnel.legacy_preserved == 1
    assert result.metrics() == {
        "candidates": {
            "proposed": 1,
            "rendered": 1,
            "accepted": 1,
            "rejected": 0,
        },
        "transaction": {"rollback_count": 1},
        "rollout": {"kill_switch_enabled": False},
    }


def test_kill_switch_is_preserved_in_canonical_pipeline_metrics(monkeypatch):
    monkeypatch.setenv("SLIDESPEC_KILL_SWITCH", "1")
    rollout = resolve_slidespec_repair_rollout(
        uuid.UUID("00000000-0000-0000-0000-000000000011"),
        slide_index=10,
        opt_in=True,
    )

    result = asyncio.run(
        SlideSpecRepairPipeline(_dependencies([])).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=rollout
        )
    )

    assert result.metrics()["rollout"] == {"kill_switch_enabled": True}


def test_publication_exception_preserves_original_without_false_rollback(monkeypatch):
    events = []
    dependencies = _dependencies(events)

    async def publish(spec, plan, artifact):
        events.append("publish")
        raise RuntimeError("injected publication fault")

    dependencies.publish = publish
    result = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_baseline(), rollout=_rollout(monkeypatch)
        )
    )

    assert result.outcomes[0].applied is False
    assert result.outcomes[0].rolled_back is False
    assert result.outcomes[0].apply_status == "failed"
    assert result.outcomes[0].reason_codes == ("artifact.publish.unexpected_error",)
    assert result.funnel.applied == 0
    assert result.funnel.rolled_back == 0
    assert result.funnel.legacy_preserved == 1


def test_twenty_slide_quality_gain_corpus_never_publishes_unmeasured_candidate(
    monkeypatch,
):
    """Golden safety corpus for best-of-candidates and fail-closed behavior."""
    published: list[tuple[int, str]] = []
    started = perf_counter()

    for slide_index in range(20):
        events = []
        dependencies = _dependencies(events)
        if slide_index % 5 == 0:
            plans = [_plan(f"unmeasured-{slide_index:02d}")]
        else:
            plans = [
                _plan(f"adequate-{slide_index:02d}"),
                _plan(
                    f"best-{slide_index:02d}",
                    operation={
                        "op": "setSpacing",
                        "target": "sem:title",
                        "gap": 8,
                    },
                ),
            ]

        async def cascade(spec, dom, plans=plans):
            return plans

        async def render(spec, plan):
            return RenderedCandidate(
                f"html:{plan.plan_id}",
                f"render:{plan.plan_id}",
                (0, 1, 0.0, 0.01, 1, plan.plan_id),
            )

        async def vqa(artifact):
            if "unmeasured" in artifact.html_hash:
                return SensorResult(True)
            return SensorResult(
                True,
                quality_gain=0.75 if "best" in artifact.html_hash else 0.45,
            )

        async def publish(spec, plan, artifact, slide_index=slide_index):
            published.append((slide_index, plan.plan_id))
            return PublicationReceipt(True, True, True, f"receipt-{slide_index}")

        dependencies.deterministic_cascade = cascade
        dependencies.render_plan = render
        dependencies.run_vqa = vqa
        dependencies.publish = publish
        result = asyncio.run(
            SlideSpecRepairPipeline(dependencies).run(
                spec=_spec(),
                baseline_dom=_baseline(),
                rollout=_rollout(monkeypatch, index=slide_index, all_slides=True),
            )
        )

        if slide_index % 5 == 0:
            assert result.funnel.applied == 0
            assert result.funnel.legacy_preserved == 1
        else:
            assert result.funnel.applied == 1
            assert result.outcomes[0].plan_id == f"best-{slide_index:02d}"
            assert result.outcomes[0].quality_gain == 0.75

    assert len(published) == 16
    assert all(plan_id == f"best-{index:02d}" for index, plan_id in published)
    assert perf_counter() - started < 5.0
