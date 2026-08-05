import asyncio
from time import perf_counter
import uuid

from models.slidespec_v1 import REPAIR_PLAN_SCHEMA, RepairPlan
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    PublicationReceipt,
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
):
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": plan_id,
            "slideId": "slide-11",
            "baseSpecRevision": 17,
            "baseSpecHash": "sha256:" + "1" * 64,
            "expectedContentDigest": "sha256:" + "2" * 64,
            "scope": ["sem:title"],
            "strategy": strategy
            or (
                "deterministic_cascade"
                if source == "deterministic"
                else "minimal_change"
            ),
            "operations": [
                operation
                or {"op": "setSpacing", "target": "sem:title", "gap": 4}
            ],
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


def test_visual_non_regression_with_zero_gain_can_publish(monkeypatch):
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

    assert result.outcomes[0].validation_status == "accepted"
    assert result.outcomes[0].quality_gain == 0.0
    assert result.outcomes[0].applied is True
    assert result.funnel.publish_active == 1
    assert "publish" in events


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


def test_overlapping_vqa_intervals_choose_minimal_change_not_point_estimate(
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

    assert ("published-plan", "small-change") in events
    assert result.outcomes[0].plan_id == "small-change"
    assert result.outcomes[0].quality_gain_interval == (0.48, 0.72)
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
