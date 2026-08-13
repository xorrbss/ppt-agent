import asyncio
from types import SimpleNamespace

from models.slidespec_v1 import (
    Bounds,
    LayoutConstraints,
    LayoutNode,
    REPAIR_PLAN_SCHEMA,
    RepairPlan,
)
from services import slidespec_authored_executor as executor
from services.slidespec_geometry_candidates import generate_geometry_candidates
from services.slidespec_pipeline_performance import (
    BoundedSlideScheduler,
    NegativeProposalCache,
    ProposalCacheKey,
    adjacent_influence_digests,
    deterministic_candidate_order,
    inspection_cache_digest,
    pareto_prune_plans,
)
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    PublicationReceipt,
    ProposalBatch,
    RenderedCandidate,
    SensorResult,
    SlideSpecInput,
    SlideSpecRepairDependencies,
    SlideSpecRepairPipeline,
)
from services.slidespec_rollout_policy import SlideSpecRepairRolloutDecision
from scripts.slidespec_pipeline_benchmark import _funnel_metric, _phase_summary
from utils.slidespec_constraints import ConstraintReport, ConstraintViolation


def _plan(plan_id: str, *, expensive: bool = False) -> RepairPlan:
    operations = [
        {"op": "setSpacing", "target": "sem:title", "gap": 4},
    ]
    if expensive:
        operations.append(
            {
                "op": "setFontScale",
                "target": "sem:title",
                "scale": 0.9,
            }
        )
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": plan_id,
            "slideId": "slide-1",
            "baseSpecRevision": 1,
            "baseSpecHash": "sha256:" + "1" * 64,
            "expectedContentDigest": "sha256:" + "2" * 64,
            "scope": ["sem:title"],
            "diagnosticRefs": ["dom.clipping:sem:title"],
            "strategy": "minimal_change",
            "operations": operations,
            "proposedBy": {"kind": "llm", "policyVersion": "test/1"},
        }
    )


def _font_plan(plan_id: str, scale: float) -> RepairPlan:
    payload = _plan(plan_id).model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["operations"] = [
        {"op": "setFontScale", "target": "sem:title", "scale": scale}
    ]
    return RepairPlan.model_validate(payload)


def _translation_plan(plan_id: str, target: str, x: float, y: float = 0.0) -> RepairPlan:
    payload = _plan(plan_id).model_dump(mode="json", by_alias=True, exclude_none=True)
    payload["scope"] = [target]
    payload["operations"] = [
        {
            "op": "translateWithinParent",
            "target": target,
            "value": {"x": x, "y": y},
        }
    ]
    return RepairPlan.model_validate(payload)


def _spec() -> SlideSpecInput:
    return SlideSpecInput(
        revision_id="slide-1:1",
        spec_hash="sha256:" + "1" * 64,
        semantic_text={"sem:title": "title"},
    )


def _dom() -> DomEvidence:
    return DomEvidence((DomDefect("dom.clipping", "high", "sem:title"),))


def _rollout() -> SlideSpecRepairRolloutDecision:
    return SlideSpecRepairRolloutDecision(
        "slidespec_active", False, False, True, True, False, 0, None
    )


def _dependencies(*, propose, preflight=None, cache=None, events=None):
    events = events if events is not None else []

    async def cascade(spec, dom):
        return ()

    async def validate(spec, plan):
        events.append(("validate", plan.plan_id))
        return SensorResult(True)

    async def render(spec, plan):
        events.append(("render", plan.plan_id))
        return RenderedCandidate(plan.plan_id, plan.plan_id)

    async def inspect(candidate):
        return DomEvidence((DomDefect("dom.clipping", "high", "sem:title"),))

    async def ocr(candidate, inventory):
        return SensorResult(True)

    async def vqa(candidate):
        return SensorResult(True, quality_gain=1.0)

    async def publish(spec, plan, candidate):
        return PublicationReceipt(True, True, True)

    return SlideSpecRepairDependencies(
        deterministic_cascade=cascade,
        validate_plan=validate,
        render_plan=render,
        inspect_dom=inspect,
        run_ocr=ocr,
        run_vqa=vqa,
        publish=publish,
        propose=propose,
        proposal_preflight=preflight,
        proposal_capability_digest="capability:v1",
        proposal_model_digest="model:v1",
        proposal_cache=cache,
    )


def test_projection_preflight_short_circuits_before_model_call():
    calls = 0

    async def propose(spec, dom):
        nonlocal calls
        calls += 1
        return ()

    async def preflight(spec, dom):
        return SensorResult(False, "proposal.mutation_envelope_unavailable")

    result = asyncio.run(
        SlideSpecRepairPipeline(
            _dependencies(propose=propose, preflight=preflight)
        ).run(spec=_spec(), baseline_dom=_dom(), rollout=_rollout())
    )

    assert calls == 0
    assert result.telemetry.llm_calls == 0
    assert result.telemetry.proposal_preflight_skips == 1
    assert result.reason_codes[0] == "proposal.mutation_envelope_unavailable"


def test_empty_proposal_negative_cache_uses_all_four_digest_dimensions():
    cache = NegativeProposalCache()
    calls = 0

    async def propose(spec, dom):
        nonlocal calls
        calls += 1
        return ()

    dependencies = _dependencies(propose=propose, cache=cache)
    first = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_dom(), rollout=_rollout()
        )
    )
    second = asyncio.run(
        SlideSpecRepairPipeline(dependencies).run(
            spec=_spec(), baseline_dom=_dom(), rollout=_rollout()
        )
    )

    assert calls == 1
    assert first.telemetry.proposal_cache_misses == 1
    assert second.telemetry.proposal_cache_hits == 1
    assert second.reason_codes[0] == "proposal.negative_cache_hit"

    base = ProposalCacheKey("spec", "dom", "cap", "model")
    cache = NegativeProposalCache()
    cache.put(base)
    assert cache.contains(base)
    assert not cache.contains(ProposalCacheKey("spec-2", "dom", "cap", "model"))
    assert not cache.contains(ProposalCacheKey("spec", "dom-2", "cap", "model"))
    assert not cache.contains(ProposalCacheKey("spec", "dom", "cap-2", "model"))
    assert not cache.contains(ProposalCacheKey("spec", "dom", "cap", "model-2"))


def test_typed_llm_funnel_and_no_candidate_reason_survive_terminal_fallback():
    async def propose(spec, dom):
        return ProposalBatch(
            no_candidate_reason="llm_schema_rejected",
            call_count=1,
            response_count=1,
            schema_rejection_count=1,
        )

    result = asyncio.run(
        SlideSpecRepairPipeline(
            _dependencies(propose=propose, cache=NegativeProposalCache())
        ).run(spec=_spec(), baseline_dom=_dom(), rollout=_rollout())
    )

    assert result.reason_codes[:2] == (
        "deterministic_no_candidate",
        "llm_schema_rejected",
    )
    assert result.telemetry.llm_calls == 1
    assert result.telemetry.llm_response_count == 1
    assert result.telemetry.llm_schema_rejections == 1
    assert result.telemetry.llm_render_count == 0
    assert result.telemetry.no_candidate_reasons == (
        "deterministic_no_candidate",
        "llm_schema_rejected",
    )


def test_preflight_unprojectable_uses_typed_no_candidate_telemetry():
    async def propose(spec, dom):
        raise AssertionError("preflight must prevent the provider call")

    async def preflight(spec, dom):
        return SensorResult(False, "proposal.projection_unavailable")

    result = asyncio.run(
        SlideSpecRepairPipeline(
            _dependencies(propose=propose, preflight=preflight)
        ).run(spec=_spec(), baseline_dom=_dom(), rollout=_rollout())
    )

    assert result.telemetry.llm_calls == 0
    assert result.telemetry.llm_preflight_pass_count == 0
    assert result.telemetry.no_candidate_reasons == (
        "deterministic_no_candidate",
        "proposal_preflight_unprojectable",
    )


def test_pareto_pruning_happens_after_validation_and_before_render():
    events = []

    async def propose(spec, dom):
        return (_font_plan("expensive", 0.9), _font_plan("cheap", 0.95))

    result = asyncio.run(
        SlideSpecRepairPipeline(
            _dependencies(
                propose=propose,
                cache=NegativeProposalCache(),
                events=events,
            )
        ).run(spec=_spec(), baseline_dom=_dom(), rollout=_rollout())
    )

    assert [event for event in events if event[0] == "validate"] == [
        ("validate", "cheap"),
        ("validate", "expensive"),
    ]
    assert [event for event in events if event[0] == "render"] == [
        ("render", "cheap")
    ]
    assert result.telemetry.pareto_pruned == 1
    assert any(
        outcome.reason_codes == ("candidate.pareto_dominated",)
        for outcome in result.outcomes
    )


def test_pareto_keeps_different_targets_and_effect_directions_incomparable():
    leaf = _translation_plan("leaf", "lay:leaf", -0.02)
    ancestor = _translation_plan("ancestor", "lay:ancestor", -0.03)
    opposite = _translation_plan("opposite", "lay:leaf", 0.02)

    survivors, pruned = pareto_prune_plans((leaf, ancestor, opposite))

    assert {plan.plan_id for plan in survivors} == {"leaf", "ancestor", "opposite"}
    assert pruned == ()


def test_same_slide_candidate_order_is_provider_order_independent():
    first = deterministic_candidate_order((_plan("b"), _plan("a")))
    second = deterministic_candidate_order((_plan("a"), _plan("b")))
    assert [plan.plan_id for plan in first] == ["a", "b"]
    assert [plan.plan_id for plan in second] == ["a", "b"]


def test_incremental_digest_invalidates_changed_slide_and_neighbors_only():
    before = adjacent_influence_digests(("a", "b", "c", "d", "e"))
    after = adjacent_influence_digests(("a", "b", "changed", "d", "e"))
    changed = {index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
    assert changed == {1, 2, 3}

    common = dict(
        spec_digest="spec",
        content_digest="content",
        source_digest="source",
        font_digest="font",
        browser_digest="browser",
        adjacent_digest="adjacent",
    )
    baseline = inspection_cache_digest(**common)
    for field in common:
        modified = {**common, field: common[field] + "-changed"}
        assert inspection_cache_digest(**modified) != baseline


def test_bounded_slide_scheduler_limits_concurrency_and_preserves_order():
    active = 0
    peak = 0

    async def scenario():
        scheduler = BoundedSlideScheduler(max_concurrency=3)

        async def worker(value):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep((7 - value) * 0.001)
            active -= 1
            return value * 10

        return await scheduler.map(tuple(range(8)), worker)

    assert asyncio.run(scenario()) == tuple(value * 10 for value in range(8))
    assert peak == 3


def test_pipeline_benchmark_summarizes_deck_wall_clock_not_slide_sum():
    runs = [
        {
            "deck_wall_ms": 100.0,
            "stage_timings_ms": {"render": 50.0, "slide_total_sum": 250.0},
            "llm_calls": 1,
            "llm_candidates": 0,
            "proposal_cache_hits": 0,
            "proposal_cache_misses": 1,
            "proposal_preflight_skips": 0,
            "inspection_cache_hits": 0,
            "inspection_cache_misses": 90,
            "pareto_pruned": 2,
        },
        {
            "deck_wall_ms": 200.0,
            "stage_timings_ms": {"render": 60.0, "slide_total_sum": 400.0},
            "llm_calls": 0,
            "llm_candidates": 0,
            "proposal_cache_hits": 1,
            "proposal_cache_misses": 0,
            "proposal_preflight_skips": 0,
            "inspection_cache_hits": 90,
            "inspection_cache_misses": 0,
            "pareto_pruned": 0,
        },
    ]
    summary = _phase_summary(runs)
    assert summary["deck_wall_ms"]["p50"] == 150.0
    assert summary["stages_ms"]["slide_total_sum"]["p50"] == 325.0
    assert summary["deck_wall_ms"]["p50"] != summary["stages_ms"]["slide_total_sum"]["p50"]


def test_pipeline_benchmark_reads_executor_validation_funnel_aliases():
    metrics = {"validation_rejected": 7, "validation_inconclusive": 3}

    assert _funnel_metric(metrics, "candidate_rejected", "validation_rejected") == 7
    assert (
        _funnel_metric(
            metrics, "candidate_inconclusive", "validation_inconclusive"
        )
        == 3
    )


def test_off_canvas_bbox_evidence_uses_canvas_overflow_not_scroll_size():
    magnitude = executor._off_canvas_fraction(
        {
            "qid": "q-footer-8ad182d251d4",
            "bbox": {"x": 120, "y": 714, "width": 600, "height": 20},
            "scroll_width": 600,
            "client_width": 600,
            "scroll_height": 20,
            "client_height": 20,
        },
        canvas_width=1280,
        canvas_height=720,
    )
    assert magnitude == 14 / 720


def test_soft_constraint_regression_preserves_exact_structured_deltas():
    request = SimpleNamespace(
        spec=SimpleNamespace(
            slideId="slide-soft-regression",
            layout=LayoutNode(
                layoutId="lay:root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId="lay:title",
                        kind="content",
                        contentSemanticId="sem:title",
                    ),
                ),
            ),
            content=(
                SimpleNamespace(
                    semanticId="sem:title", text="Title", readingOrder=0
                ),
            ),
        )
    )
    baseline = ConstraintReport(
        violations=(
            ConstraintViolation(
                "con:title",
                "readability",
                "constraint.readability.violated",
                "soft",
                20,
                0.5,
                ("lay:title",),
                magnitude=0.1,
                detail="baseline",
            ),
        )
    )
    candidate = ConstraintReport(
        violations=(
            ConstraintViolation(
                "con:title",
                "readability",
                "constraint.readability.violated",
                "soft",
                20,
                0.5,
                ("lay:title",),
                magnitude=0.4,
                detail="candidate",
            ),
        )
    )

    evidence = executor._soft_constraint_regression_evidence(
        request, baseline, candidate
    )
    payload = evidence.defects[0].diagnostics["soft_constraint_regression"]

    assert payload["baseline_soft_cost"] == 1.0
    assert payload["candidate_soft_cost"] == 4.0
    assert payload["regressed_constraint_ids"] == ["con:title"]
    assert payload["constraint_deltas"] == [
        {
            "constraint_id": "con:title",
            "kind": "readability",
            "targets": ["lay:title"],
            "before_magnitude": 0.1,
            "after_magnitude": 0.4,
            "before_weighted_cost": 1.0,
            "after_weighted_cost": 4.0,
            "weighted_cost_delta": 3.0,
            "before_details": ["baseline"],
            "after_details": ["candidate"],
        }
    ]


def test_single_qid_owned_paint_uses_bound_occluder_fallback(monkeypatch):
    class FakeRequest:
        pass

    target = LayoutNode(
        layoutId="lay:target",
        kind="content",
        contentSemanticId="sem:target",
        bounds=Bounds(x=0.1, y=0.1, width=0.4, height=0.2),
        constraints=LayoutConstraints(
            allowedOperations=("translateWithinParent",), maxTranslation=0.5
        ),
    )
    peer = LayoutNode(
        layoutId="lay:peer",
        kind="content",
        contentSemanticId="sem:peer",
        bounds=Bounds(x=0.3, y=0.15, width=0.4, height=0.2),
        constraints=LayoutConstraints(
            allowedOperations=("translateWithinParent",), maxTranslation=0.5
        ),
    )
    wrapper = LayoutNode(
        layoutId="lay:wrapper",
        kind="content",
        contentSemanticId="sem:wrapper",
        bounds=Bounds(x=0.05, y=0.05, width=0.6, height=0.3),
        constraints=LayoutConstraints(
            allowedOperations=("translateWithinParent",), maxTranslation=0.5
        ),
    )
    title = LayoutNode(
        layoutId="lay:title",
        kind="content",
        contentSemanticId="sem:title",
        bounds=Bounds(x=0.1, y=0.05, width=0.4, height=0.1),
        constraints=LayoutConstraints(
            allowedOperations=("setFontScale",),
            minFontScale=0.9,
            maxFontShrink=0.1,
        ),
    )
    request = FakeRequest()
    target_parent = LayoutNode(
        layoutId="lay:target-parent",
        kind="frame",
        bounds=Bounds(x=0.0, y=0.0, width=1.0, height=1.0),
        children=(target,),
    )
    peer_parent = LayoutNode(
        layoutId="lay:peer-parent",
        kind="frame",
        bounds=Bounds(x=0.0, y=0.0, width=1.0, height=1.0),
        children=(peer,),
    )
    request.spec = SimpleNamespace(
        slideId="slide-overlap",
        specRevision=1,
        digests=SimpleNamespace(spec="spec", content="content"),
        layout=LayoutNode(
            layoutId="lay:root",
            kind="canvas",
            children=(target_parent, peer_parent, wrapper, title),
        ),
        content=(
            SimpleNamespace(semanticId="sem:target", kind="text"),
            SimpleNamespace(semanticId="sem:peer", kind="shape"),
            SimpleNamespace(semanticId="sem:wrapper", kind="group"),
            SimpleNamespace(semanticId="sem:title", kind="title"),
        ),
        constraints=(),
    )
    request.source = SimpleNamespace(
        viewportWidth=1280,
        viewportHeight=720,
        bindings=(
            SimpleNamespace(
                semanticId="sem:target",
                layoutId="lay:target",
                qid="q-target",
                staticTranslationSafe=True,
            ),
            SimpleNamespace(
                semanticId="sem:peer", layoutId="lay:peer", qid="q-cover"
            ),
            SimpleNamespace(
                semanticId="sem:wrapper",
                layoutId="lay:wrapper",
                qid="q-wrapper",
                staticTranslationSafe=True,
            ),
            SimpleNamespace(
                semanticId="sem:title",
                layoutId="lay:title",
                qid="q-title",
                fontSizePx=32.0,
            ),
        ),
    )
    monkeypatch.setattr(executor, "RenderRequest", FakeRequest)

    context = executor._repair_context(
        request,
        DomEvidence(
            (
                DomDefect(
                    "dom.unintended_overlap",
                    "medium",
                    "sem:target",
                    qid="q-target",
                ),
            )
        ),
        {
            "elements": [
                {
                    "qid": "q-target",
                    "parent_qid": "q-wrapper",
                    "bbox": {"x": 58, "y": 144.625, "width": 561, "height": 22.5},
                    "occluded_fraction": 0.7,
                    "continuous_occluded_fraction": 0.6,
                    "occlusion_probe_version": "owned-paint-v3",
                    "occlusion_occluder_qids": ["q-cover"],
                },
                {
                    "qid": "q-cover",
                    "bbox": {"x": 76, "y": 145, "width": 114, "height": 20},
                    "has_owned_paint": True,
                },
                {
                    "qid": "q-wrapper",
                    "parent_qid": "q-root",
                    "bbox": {"x": 58, "y": 33, "width": 561, "height": 134.125},
                },
                {
                    "qid": "q-title",
                    "parent_qid": "q-wrapper",
                    "bbox": {"x": 128, "y": 50, "width": 512, "height": 72},
                    "text_fragments": [
                        {"x": 128, "y": 48, "width": 440, "height": 40},
                        {"x": 128, "y": 82, "width": 420, "height": 40},
                    ],
                    "font_loaded": True,
                    "font_size": 32,
                },
            ]
        },
    )

    issue = context["geometryIssues"][0]
    assert issue["qid"] == "q-target"
    assert issue["otherTarget"] == "lay:peer"
    assert issue["constraintId"] == (
        "dom:dom.unintended_overlap:q-cover:q-target"
    )
    assert issue["sameCoordinateSpace"] is True
    assert issue["containerBounds"] == {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
    }
    assert issue["detectorEvidence"]["occluderQid"] == "q-cover"
    ancestor = issue["boundAncestors"][0]
    assert ancestor["qid"] == "q-wrapper"
    assert ancestor["ownershipProven"] is True
    assert ancestor["browserProof"]["parentQidChain"] == (
        "q-target",
        "q-wrapper",
    )
    assert ancestor["candidateDeltas"]
    assert all(item["source"] == "chrome" for item in ancestor["candidateDeltas"])
    assert issue["issueGroupId"] in context["geometryIssueGroups"]
    assert (
        f"issue-group:{issue['issueGroupId']}"
        in context["diagnosticRefsByTarget"]["lay:target"]
    )
    glyph_composition = context["geometryGlyphCompositions"][0]
    assert glyph_composition["issueGroupIds"] == (issue["issueGroupId"],)
    assert glyph_composition["required"] is True
    assert glyph_composition["glyph"]["qid"] == "q-title"
    assert glyph_composition["glyph"]["candidateScale"] == 0.975
    assert glyph_composition["glyph"]["measurement"]["kind"] == "range-bounds"
    assert [move["role"] for move in glyph_composition["moves"]] == [
        "owner",
        "subtitle",
    ]
    assert glyph_composition["proof"]["candidateResolvesIssue"] is True
    assert glyph_composition["proof"]["requiredPostconditions"] == {
        "domNoNewIssue": True,
        "hardFailure": False,
        "softCostMaximum": 0.0,
        "unknownHard": [],
        "roundTripErrors": [],
    }


    constraint_context = executor._repair_context(
        request,
        DomEvidence(),
        {
            "elements": [
                {"qid": "q-target", "bbox": {"x": 128, "y": 72, "width": 512, "height": 144}},
                {"qid": "q-cover", "bbox": {"x": 384, "y": 108, "width": 512, "height": 144}},
            ]
        },
        ConstraintReport(
            (
                ConstraintViolation(
                    constraint_id="con:imported-global",
                    kind="nonOverlap",
                    code="constraint.non_overlap.violated",
                    strength="hard",
                    priority=100,
                    confidence=1.0,
                    targets=("lay:target", "lay:peer"),
                    magnitude=0.2,
                ),
            )
        ),
    )
    invariant_issue = next(
        item
        for item in constraint_context["geometryIssues"]
        if item["constraintId"] == "con:imported-global"
    )
    assert invariant_issue["sameCoordinateSpace"] is True

    composed = executor._repair_context(
        request,
        DomEvidence(
            (
                DomDefect(
                    "dom.unintended_overlap",
                    "medium",
                    "sem:target",
                    qid="q-target",
                ),
                DomDefect(
                    "dom.unintended_overlap",
                    "medium",
                    "sem:peer",
                    qid="q-cover",
                ),
                DomDefect(
                    "dom.unintended_overlap",
                    "medium",
                    "sem:wrapper",
                    ("sem:peer",),
                    "q-wrapper",
                ),
            )
        ),
        {
            "elements": [
                {
                    "qid": "q-target",
                    "parent_qid": "q-wrapper",
                    "bbox": {"x": 128, "y": 72, "width": 512, "height": 144},
                    "occluded_fraction": 0.7,
                    "continuous_occluded_fraction": 0.6,
                    "occlusion_probe_version": "owned-paint-v3",
                    "occlusion_occluder_qids": ["q-cover"],
                },
                {
                    "qid": "q-cover",
                    "bbox": {"x": 384, "y": 108, "width": 512, "height": 144},
                    "occluded_fraction": 0.6,
                    "continuous_occluded_fraction": 0.5,
                    "occlusion_probe_version": "owned-paint-v3",
                    "occlusion_occluder_qids": ["q-target"],
                },
                {
                    "qid": "q-wrapper",
                    "parent_qid": "q-root",
                    "bbox": {"x": 64, "y": 36, "width": 768, "height": 216},
                    "occluded_fraction": 0.5,
                    "continuous_occluded_fraction": 0.4,
                    "occlusion_probe_version": "owned-paint-v3",
                    "occlusion_occluder_qids": ["q-cover"],
                },
            ]
        },
    )
    required = set(composed["requiredGeometryIssueGroups"])
    groups_by_qid = {
        item["qid"]: item["issueGroupId"]
        for item in composed["geometryIssues"]
    }
    assert groups_by_qid["q-target"] in required
    assert groups_by_qid["q-cover"] in required
    assert groups_by_qid["q-wrapper"] not in required
    assert any(
        set(group_ids) == required
        for group_ids in composed["geometryCompositionGroups"].values()
    )
    aggregate = next(
        item for item in composed["geometryIssues"] if item["qid"] == "q-wrapper"
    )
    coordinated = aggregate["coordinatedAncestorMoves"][0]
    proof = coordinated["proof"]
    axis = proof["axis"]
    target_delta = coordinated["moves"][0]["deltaPx"][axis]
    peer_delta = coordinated["moves"][1]["deltaPx"][axis]
    assert proof["pairResolvesIssue"] is True
    assert abs(target_delta - peer_delta) >= proof["separationPx"] - 1e-6
    generated = generate_geometry_candidates(composed)
    coordinated_candidates = [
        candidate
        for candidate in generated.candidates
        if candidate.kind == "coordinatedAncestorMoves"
        and candidate.qid == "q-wrapper"
    ]
    assert coordinated_candidates
    assert {
        operation["target"]
        for operation in coordinated_candidates[0].operations
    } == {"lay:wrapper", "lay:peer"}
    promoted_leaf_issue = next(
        candidate
        for candidate in generated.candidates
        if candidate.kind == "coordinatedAncestorMoves"
        and candidate.qid == "q-target"
    )
    assert {
        operation["target"] for operation in promoted_leaf_issue.operations
    } == {"lay:wrapper", "lay:peer"}


def test_exact_bottom_stack_context_preserves_anchor_and_adjacency(monkeypatch):
    class FakeRequest:
        pass

    panel = LayoutNode(
        layoutId="lay:panel",
        kind="content",
        contentSemanticId="sem:panel",
        bounds=Bounds(x=0.0375, y=664 / 720, width=0.925, height=50 / 720),
        constraints=LayoutConstraints(
            allowedOperations=("translateWithinParent",), maxTranslation=0.08
        ),
    )
    footer = LayoutNode(
        layoutId="lay:footer",
        kind="content",
        contentSemanticId="sem:footer",
        bounds=Bounds(x=0.0375, y=714 / 720, width=0.925, height=6 / 720),
        constraints=LayoutConstraints(
            allowedOperations=("translateWithinParent",), maxTranslation=0.08
        ),
    )
    request = FakeRequest()
    request.spec = SimpleNamespace(
        slideId="slide-footer",
        specRevision=1,
        digests=SimpleNamespace(spec="spec", content="content"),
        layout=LayoutNode(
            layoutId="lay:root", kind="canvas", children=(panel, footer)
        ),
        content=(
            SimpleNamespace(semanticId="sem:panel", kind="group"),
            SimpleNamespace(semanticId="sem:footer", kind="text"),
        ),
        constraints=(),
    )
    request.source = SimpleNamespace(
        viewportWidth=1280,
        viewportHeight=720,
        bindings=(
            SimpleNamespace(
                semanticId="sem:panel",
                layoutId="lay:panel",
                qid="q-panel",
                position="static",
                transform="none",
                staticTranslationSafe=True,
                topPx=None,
                bottomPx=None,
                sourceBoundsPx=SimpleNamespace(x=48, y=664, width=1184, height=50),
            ),
            SimpleNamespace(
                semanticId="sem:footer",
                layoutId="lay:footer",
                qid="q-footer",
                position="fixed",
                transform="none",
                staticTranslationSafe=False,
                topPx=None,
                bottomPx=-14,
                sourceBoundsPx=SimpleNamespace(x=48, y=714, width=1184, height=20),
            ),
        ),
    )
    monkeypatch.setattr(executor, "RenderRequest", FakeRequest)
    context = executor._repair_context(
        request,
        DomEvidence(
            (DomDefect("dom.off_canvas", "high", "sem:footer", qid="q-footer"),)
        ),
        {
            "width": 1280,
            "height": 720,
            "elements": [
                {
                    "qid": "q-panel",
                    "parent_qid": "q-root",
                    "bbox": {"x": 48, "y": 664, "width": 1184, "height": 50},
                    "static_translation_probe": {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "stackingStable": True,
                        "safeTranslationIntervalPx": {
                            "minX": -48,
                            "maxX": 48,
                            "minY": -100,
                            "maxY": 6,
                        },
                    },
                },
                {
                    "qid": "q-footer",
                    "parent_qid": "q-root",
                    "bbox": {"x": 48, "y": 714, "width": 1184, "height": 20},
                    "containing_block_proof": {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "exactBoundOffsetParent": True,
                        "offsetParentQid": "q-root",
                    },
                },
            ],
        },
    )

    composition = context["geometryExactCompositions"][0]
    assert composition["required"] is True
    assert set(composition["issueGroupIds"]).intersection(
        context["requiredGeometryIssueGroups"]
    )
    assert [operation["valuePx"]["y"] for operation in composition["operations"]] == [
        -14.0,
        -14.0,
    ]
    assert composition["operations"][1]["projectionProof"]["verticalAnchor"] == "bottom"
    assert composition["proof"]["candidateResolvesIssue"] is True
    assert composition["proof"]["relationships"][0]["candidateGapPx"] == 0


def test_executor_target_delta_requires_same_qid_and_full_resolution(monkeypatch):
    semantic_id = "sem:footer"
    layout_id = "lay:footer"
    request = SimpleNamespace(
        spec=SimpleNamespace(
            layout=LayoutNode(
                layoutId="lay:root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId=layout_id,
                        kind="content",
                        contentSemanticId=semantic_id,
                        bounds=Bounds(x=0.1, y=0.9, width=0.8, height=0.05),
                    ),
                ),
            ),
            content=(SimpleNamespace(semanticId=semantic_id),),
        )
    )
    monkeypatch.setattr(
        executor,
        "_qid_to_semantic",
        lambda _request: {"q-footer-8ad182d251d4": semantic_id},
    )
    plan = RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": "footer-fix",
            "slideId": "slide-19",
            "baseSpecRevision": 1,
            "baseSpecHash": "sha256:" + "1" * 64,
            "expectedContentDigest": "sha256:" + "2" * 64,
            "scope": [layout_id],
            "diagnosticRefs": [
                "code:dom.off_canvas",
                f"semantic:{semantic_id}",
                "qid:q-footer-8ad182d251d4",
            ],
            "strategy": "deterministic_cascade",
            "operations": [
                {
                    "op": "translateWithinParent",
                    "target": layout_id,
                    "value": {"x": 0, "y": -0.02},
                }
            ],
            "proposedBy": {"kind": "rule_engine", "policyVersion": "test/1"},
        }
    )
    baseline = DomEvidence(
        (
            DomDefect(
                "dom.off_canvas",
                "high",
                semantic_id,
                qid="q-footer-8ad182d251d4",
            ),
        )
    )

    required, resolved = executor._target_issue_evidence(
        request=request,
        plan=plan,
        baseline_dom=baseline,
        candidate_dom=DomEvidence(),
        baseline_constraints=ConstraintReport(),
        candidate_constraints=ConstraintReport(),
    )
    assert required[0].qid == "q-footer-8ad182d251d4"
    assert resolved is not None and resolved[0].after == 0.0

    _, partial = executor._target_issue_evidence(
        request=request,
        plan=plan,
        baseline_dom=baseline,
        candidate_dom=DomEvidence(
            (
                DomDefect(
                    "dom.off_canvas",
                    "medium",
                    semantic_id,
                    qid="q-footer-8ad182d251d4",
                ),
            )
        ),
        baseline_constraints=ConstraintReport(),
        candidate_constraints=ConstraintReport(),
    )
    assert partial is not None and partial[0].after == 1.0

    _, drifted = executor._target_issue_evidence(
        request=request,
        plan=plan,
        baseline_dom=baseline,
        candidate_dom=DomEvidence(
            (
                DomDefect(
                    "dom.off_canvas",
                    "medium",
                    semantic_id,
                    qid="q-different-element",
                ),
            )
        ),
        baseline_constraints=ConstraintReport(),
        candidate_constraints=ConstraintReport(),
    )
    assert drifted is not None and drifted[0].after is None


def test_executor_target_delta_includes_descendant_browser_defects(monkeypatch):
    owner_semantic = "sem:footer"
    first_semantic = "sem:footer/span-a"
    second_semantic = "sem:footer/span-b"
    owner_layout = "lay:footer"
    first_layout = "lay:footer/span-a"
    second_layout = "lay:footer/span-b"
    request = SimpleNamespace(
        spec=SimpleNamespace(
                layout=LayoutNode(
                    layoutId="lay:root",
                    kind="canvas",
                    children=(
                        LayoutNode(
                            layoutId=owner_layout,
                            kind="content",
                            contentSemanticId=owner_semantic,
                            bounds=Bounds(x=0.1, y=0.9, width=0.8, height=0.05),
                        ),
                        LayoutNode(
                            layoutId=first_layout,
                            kind="content",
                            contentSemanticId=first_semantic,
                            bounds=Bounds(x=0.1, y=0.9, width=0.3, height=0.05),
                        ),
                        LayoutNode(
                            layoutId=second_layout,
                            kind="content",
                            contentSemanticId=second_semantic,
                            bounds=Bounds(x=0.4, y=0.9, width=0.3, height=0.05),
                        ),
                    ),
                ),
                content=(
                    SimpleNamespace(
                        semanticId=owner_semantic,
                        parentSemanticId=None,
                    ),
                    SimpleNamespace(
                        semanticId=first_semantic,
                        parentSemanticId=owner_semantic,
                    ),
                    SimpleNamespace(
                        semanticId=second_semantic,
                        parentSemanticId=owner_semantic,
                    ),
                ),
        )
    )
    monkeypatch.setattr(
        executor,
        "_qid_to_semantic",
        lambda _request: {
            "q-footer": owner_semantic,
            "q-span-a": first_semantic,
            "q-span-b": second_semantic,
        },
    )
    plan = RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": "footer-owner-fix",
            "slideId": "slide-19",
            "baseSpecRevision": 1,
            "baseSpecHash": "sha256:" + "1" * 64,
            "expectedContentDigest": "sha256:" + "2" * 64,
            "scope": [owner_layout],
            "diagnosticRefs": [
                "code:dom.off_canvas",
                f"semantic:{owner_semantic}",
                "qid:q-footer",
            ],
            "strategy": "deterministic_cascade",
            "operations": [
                {
                    "op": "translateWithinParent",
                    "target": owner_layout,
                    "value": {"x": 0, "y": -0.02},
                }
            ],
            "proposedBy": {"kind": "rule_engine", "policyVersion": "test/1"},
        }
    )
    baseline = DomEvidence(
        (
            DomDefect("dom.off_canvas", "high", owner_semantic, qid="q-footer"),
            DomDefect("dom.clipping", "high", first_semantic, qid="q-span-a"),
            DomDefect("dom.clipping", "medium", second_semantic, qid="q-span-b"),
        )
    )

    required, deltas = executor._target_issue_evidence(
        request=request,
        plan=plan,
        baseline_dom=baseline,
        candidate_dom=DomEvidence(),
        baseline_constraints=ConstraintReport(),
        candidate_constraints=ConstraintReport(),
    )

    assert [identity.qid for identity in required] == [
        "q-footer",
        "q-span-a",
        "q-span-b",
    ]
    assert all(identity.constraint_id for identity in required)
    assert [identity.constraint_id for identity in required] == [
        "dom:dom.off_canvas:q-footer",
        "dom:dom.clipping:q-span-a",
        "dom:dom.clipping:q-span-b",
    ]
    assert deltas is not None
    assert [(delta.before, delta.after) for delta in deltas] == [
        (2.0, 0.0),
        (2.0, 0.0),
        (1.0, 0.0),
    ]
