import pytest

from models.slidespec_v1 import (
    REPAIR_PLAN_SCHEMA,
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    LayoutConstraints,
    LayoutNode,
    RenderRequest,
    RendererManifest,
    RepairPlan,
    SlideDigests,
    SlideSpec,
)
from services.slidespec_repair_patterns import (
    PATTERN_POLICY_VERSION,
    PatternObservation,
    RepairPatternRegistry,
    pattern_observation,
    repair_diagnostic_family,
    repair_pattern_key,
)
from services.slidespec_repair_pipeline import DomDefect, DomEvidence
from utils.slidespec_core import ZERO_DIGEST, seal_slidespec


def _request(
    index: int,
    *,
    wrapped: bool = False,
    bounds: Bounds | None = None,
    text: str | None = None,
    constraints: LayoutConstraints | None = None,
) -> RenderRequest:
    semantic_id = f"sem:title-{index}"
    title = LayoutNode(
        layoutId=f"lay:title-{index}",
        kind="content",
        contentSemanticId=semantic_id,
        bounds=bounds or Bounds(x=0.1, y=0.1, width=0.8, height=0.1),
        constraints=constraints,
    )
    children = (
        (
            LayoutNode(
                layoutId=f"lay:wrapper-{index}",
                kind="frame",
                bounds=Bounds(x=0.05, y=0.05, width=0.9, height=0.9),
                children=(title,),
            ),
        )
        if wrapped
        else (title,)
    )
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId=f"slide-{index}",
            content=(
                ContentNode(
                    semanticId=semantic_id,
                    kind="title",
                    text=text or f"Title {index}",
                ),
            ),
            layout=LayoutNode(
                layoutId=f"lay:root-{index}",
                kind="canvas",
                children=children,
            ),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    empty = "sha256:" + "0" * 64
    return RenderRequest(
        spec=spec,
        renderer=RendererManifest(
            name="slidespec-html", version="1", runtime="python"
        ),
        fonts=FontManifest(digest=empty),
        assets=AssetManifest(digest=empty),
    )


def _evidence(index: int) -> DomEvidence:
    return DomEvidence(
        (DomDefect("dom.clipping", "high", f"sem:title-{index}"),)
    )


def _plan(request: RenderRequest) -> RepairPlan:
    semantic_id = request.spec.content[0].semanticId
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": f"rp-source-{request.spec.slideId}",
            "slideId": request.spec.slideId,
            "baseSpecRevision": request.spec.specRevision,
            "baseSpecHash": request.spec.digests.spec,
            "expectedContentDigest": request.spec.digests.content,
            "scope": [semantic_id],
            "strategy": "deterministic_cascade",
            "operations": [
                {
                    "op": "setFontScale",
                    "target": semantic_id,
                    "scale": 0.92,
                    "precondition": {"currentScale": 1.0},
                }
            ],
            "limits": {
                "maxOperations": 4,
                "maxAffectedBlocks": 2,
                "maxLayoutDepth": 8,
            },
            "proposedBy": {
                "kind": "rule_engine",
                "policyVersion": "test-source/1",
            },
        }
    )


def _bounds_plan(request: RenderRequest, bounds: Bounds) -> RepairPlan:
    semantic_id = request.spec.content[0].semanticId
    payload = _plan(request).model_dump(mode="json", by_alias=True)
    payload["planId"] = f"rp-bounds-{request.spec.slideId}"
    payload["operations"] = [
        {
            "op": "setBounds",
            "target": semantic_id,
            "bounds": bounds.model_dump(mode="json"),
        }
    ]
    return RepairPlan.model_validate(payload)


def _prepare(registry: RepairPatternRegistry, *requests: RenderRequest) -> str:
    observations = tuple(
        pattern_observation(position, request, _evidence(position + 1))
        for position, request in enumerate(requests)
    )
    assert all(observation is not None for observation in observations)
    registry.prepare(tuple(observation for observation in observations if observation))
    return str(observations[0].pattern_key)  # type: ignore[union-attr]


def test_pattern_key_ignores_slide_local_ids_and_bounds_but_keeps_strict_structure() -> None:
    first = _request(1)
    second = _request(2, bounds=Bounds(x=0.2, y=0.2, width=0.6, height=0.2))
    wrapped = _request(3, wrapped=True)
    constrained = _request(
        4,
        constraints=LayoutConstraints(allowedOperations=("setFontScale",)),
    )
    dense = _request(5, text="dense " * 80)

    assert repair_pattern_key(first, _evidence(1)) == repair_pattern_key(
        second, _evidence(2)
    )
    assert repair_pattern_key(first, _evidence(1)) != repair_pattern_key(
        wrapped, _evidence(3)
    )
    assert repair_pattern_key(first, _evidence(1)) != repair_pattern_key(
        constrained, _evidence(4)
    )
    assert repair_pattern_key(first, _evidence(1)) != repair_pattern_key(
        dense, _evidence(5)
    )


def test_pattern_key_distinguishes_open_and_empty_mutation_envelopes() -> None:
    unrestricted = _request(1, constraints=LayoutConstraints())
    immutable = _request(
        2, constraints=LayoutConstraints(allowedOperations=())
    )

    assert repair_pattern_key(unrestricted, _evidence(1)) != repair_pattern_key(
        immutable, _evidence(2)
    )


def test_coarse_family_never_crosses_strict_recipe_publication_boundary() -> None:
    first = _request(1)
    constrained = _request(
        2, constraints=LayoutConstraints(allowedOperations=("setFontScale",))
    )
    assert repair_diagnostic_family(first, _evidence(1)) == repair_diagnostic_family(
        constrained, _evidence(2)
    )
    first_key = repair_pattern_key(first, _evidence(1))
    constrained_key = repair_pattern_key(constrained, _evidence(2))
    assert first_key != constrained_key

    registry = RepairPatternRegistry()
    first_observation = pattern_observation(0, first, _evidence(1))
    constrained_observation = pattern_observation(1, constrained, _evidence(2))
    assert first_observation is not None and constrained_observation is not None
    registry.prepare((first_observation, constrained_observation))
    registry.record_applied(first_key, first, _plan(first))

    assert registry.instantiate(first_key, constrained) == ()
    assert registry.instantiate(constrained_key, constrained) == ()
    assert registry.metrics()["pattern_diagnostic_family_count"] == 1
    assert registry.metrics()["pattern_cluster_count"] == 2


def test_recipe_is_provisional_on_representatives_then_active_for_cluster() -> None:
    first = _request(1)
    second = _request(2)
    registry = RepairPatternRegistry()
    key = _prepare(registry, first, second)

    registry.record_applied(key, first, _plan(first))
    rebound = registry.instantiate(key, second)

    assert len(rebound) == 1
    plan = rebound[0]
    assert plan.slide_id == second.spec.slideId
    assert plan.operations[0].target == second.spec.content[0].semanticId
    assert plan.operations[0].scale == 0.92
    assert plan.operations[0].precondition is None
    assert plan.provenance.policy_version == PATTERN_POLICY_VERSION

    registry.record_applied(key, second, plan)
    metrics = registry.metrics()
    assert metrics["pattern_recipe_count"] == 1
    assert metrics["pattern_representative_count"] == 2
    assert metrics["pattern_reuse_applied"] == 1


def test_low_mid_high_pressure_representatives_gate_nonrepresentative_reuse() -> None:
    requests = tuple(_request(index + 1) for index in range(4))
    key = repair_pattern_key(requests[0], _evidence(1))
    observations = tuple(
        PatternObservation(
            position=index,
            slide_id=request.spec.slideId,
            pattern_key=key,
            text_density=float(index + 1),
            available_space=0.5,
        )
        for index, request in enumerate(requests)
    )
    registry = RepairPatternRegistry()
    registry.prepare(observations, elapsed_ms=12.5, cache_hits=1, cache_misses=3)

    assert registry.representative_waves() == ((0,), (1,), (3,))
    registry.record_applied(key, requests[0], _plan(requests[0]))
    assert registry.instantiate(key, requests[2]) == ()

    for index in (1, 3):
        rebound = registry.instantiate(key, requests[index])
        assert len(rebound) == 1
        registry.record_applied(key, requests[index], rebound[0])

    assert registry.instantiate(key, requests[2])
    metrics = registry.metrics()
    assert metrics["pattern_recipe_count"] == 1
    assert metrics["pattern_preinspection_ms"] == 12.5
    assert metrics["pattern_preinspection_cache_misses"] == 3


def test_singleton_cluster_bypasses_recipe_learning() -> None:
    request = _request(1)
    registry = RepairPatternRegistry()
    key = _prepare(registry, request)

    registry.record_applied(key, request, _plan(request))

    assert registry.instantiate(key, request) == ()
    assert registry.representative_waves() == ()
    assert registry.metrics()["pattern_singleton_count"] == 1
    assert registry.metrics()["pattern_recipe_candidate_count"] == 0


def test_llm_proposal_is_claimed_once_per_strict_pattern() -> None:
    registry = RepairPatternRegistry()

    assert registry.claim_llm("pattern:a") is True
    assert registry.claim_llm("pattern:a") is False
    assert registry.claim_llm("pattern:b") is True


def test_set_bounds_recipe_recomputes_delta_from_target_slide_geometry() -> None:
    first = _request(1)
    second = _request(
        2, bounds=Bounds(x=0.2, y=0.2, width=0.5, height=0.2)
    )
    registry = RepairPatternRegistry()
    key = _prepare(registry, first, second)
    registry.record_applied(
        key,
        first,
        _bounds_plan(first, Bounds(x=0.1, y=0.1, width=0.8, height=0.15)),
    )

    rebound = registry.instantiate(key, second)

    assert len(rebound) == 1
    bounds = rebound[0].operations[0].bounds
    assert bounds is not None
    assert bounds.x == 0.2
    assert bounds.y == 0.2
    assert bounds.width == 0.5
    assert bounds.height == pytest.approx(0.3)


def test_exact_selector_rejects_structure_drift_even_when_key_is_mislabelled() -> None:
    first = _request(1)
    wrapped = _request(2, wrapped=True)
    key = repair_pattern_key(first, _evidence(1))
    registry = RepairPatternRegistry()
    registry.prepare(
        (
            PatternObservation(0, first.spec.slideId, key, 1.0, 0.5),
            PatternObservation(1, wrapped.spec.slideId, key, 2.0, 0.4),
        )
    )
    registry.record_applied(key, first, _plan(first))

    assert registry.instantiate(key, wrapped) == ()
