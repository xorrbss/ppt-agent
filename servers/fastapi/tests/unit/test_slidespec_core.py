from copy import deepcopy

import pytest
from pydantic import ValidationError

from models.slidespec_v1 import (
    ACCEPTANCE_SCHEMA,
    REPAIR_PLAN_SCHEMA,
    SLIDESPEC_SCHEMA,
    SLIDESPEC_SCHEMA_V1_0,
    AcceptanceDecision,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    LayoutNode,
    LayoutConstraints,
    LayoutIntent,
    Provenance,
    RenderRequest,
    RendererManifest,
    RepairPlan,
    SlideDigests,
    SlideSpec,
)
from utils.slidespec_core import (
    ZERO_DIGEST,
    SlideSpecError,
    apply_repair_plan,
    canonical_json,
    render_slidespec,
    seal_slidespec,
    validate_slidespec,
)


def _spec(*, spacing: float = 20) -> SlideSpec:
    draft = SlideSpec(
        schemaUri=SLIDESPEC_SCHEMA,
        specRevision=1,
        slideId="slide-1",
        content=(ContentNode(semanticId="sem:slide-1/title", kind="title", text="A < B"),),
        layout=LayoutNode(
            layoutId="lay:slide-1/root",
            kind="canvas",
            children=(LayoutNode(
                layoutId="lay:slide-1/title",
                kind="content",
                contentSemanticId="sem:slide-1/title",
                bounds=Bounds(x=0.1, y=0.1, width=0.8, height=0.2),
                spacing=spacing,
            ),),
        ),
        provenance=Provenance(source="native"),
        digests=SlideDigests(
            content=ZERO_DIGEST,
            hierarchy=ZERO_DIGEST,
            importantTokens=ZERO_DIGEST,
            spec=ZERO_DIGEST,
        ),
    )
    return seal_slidespec(draft)


def _plan(base: SlideSpec, operations: list[dict], **updates: object) -> RepairPlan:
    data = {
        "$schema": REPAIR_PLAN_SCHEMA,
        "planId": "rp-1",
        "slideId": base.slideId,
        "baseSpecRevision": base.specRevision,
        "baseSpecHash": base.digests.spec,
        "expectedContentDigest": base.digests.content,
        "scope": ["sem:slide-1/title"],
        "diagnosticRefs": ["dom.clipping.title"],
        "strategy": "minimal_change",
        "operations": operations,
        "limits": {"maxOperations": 8, "maxAffectedBlocks": 4},
        "proposedBy": {"kind": "rule_engine", "policyVersion": "repair-cascade/1.0"},
    }
    data.update(updates)
    return RepairPlan.model_validate(data)


def test_architecture_aliases_apply_transactionally_and_preserve_invariants() -> None:
    base = _spec()
    before = base.model_dump(mode="json", by_alias=True)
    plan = _plan(base, [
        {"op": "setSpacing", "target": "sem:slide-1/title", "gap": 12,
         "precondition": {"currentGap": 20}},
        {"op": "setFontScale", "target": "sem:slide-1/title", "scale": 0.94,
         "bounds": {"minPx": 18, "maxPx": 34},
         "precondition": {"currentScale": 1}},
    ])

    encoded = plan.model_dump(mode="json", by_alias=True)
    assert encoded["operations"][0]["gap"] == 12
    assert encoded["operations"][1]["scale"] == 0.94
    assert encoded["limits"]["maxTouchedLayoutNodes"] == 4
    assert encoded["proposedBy"]["kind"] == "rule_engine"

    applied = apply_repair_plan(base, plan)
    leaf = applied.spec.layout.children[0]
    assert (leaf.spacing, leaf.fontScale) == (12, 0.94)
    assert applied.touchedLayoutIds == ("lay:slide-1/title",)
    assert applied.spec.specRevision == 2
    assert applied.spec.digests.content == base.digests.content
    assert applied.spec.digests.hierarchy == base.digests.hierarchy
    assert base.model_dump(mode="json", by_alias=True) == before


def test_stale_or_failed_plan_never_mutates_base() -> None:
    base = _spec()
    snapshot = base.model_dump(mode="json", by_alias=True)
    stale = _plan(base, [{"op": "setSpacing", "target": "sem:slide-1/title", "gap": 12}],
                  baseSpecRevision=2)
    with pytest.raises(SlideSpecError, match="repair.base_revision.stale"):
        apply_repair_plan(base, stale)
    failing = _plan(base, [
        {"op": "setSpacing", "target": "sem:slide-1/title", "gap": 12},
        {"op": "setFontScale", "target": "lay:missing", "scale": 0.9},
    ], scope=["sem:slide-1/title", "lay:missing"])
    with pytest.raises(SlideSpecError, match="repair.target.missing"):
        apply_repair_plan(base, failing)
    assert base.model_dump(mode="json", by_alias=True) == snapshot


def test_closed_schema_rejects_raw_content_and_bad_ids() -> None:
    base = _spec()
    with pytest.raises(ValidationError):
        _plan(base, [{"op": "setText", "target": "sem:slide-1/title", "text": "new"}])
    with pytest.raises(ValidationError):
        _plan(base, [{"op": "setSpacing", "target": "sem:slide-1/title", "gap": 1,
                      "css": "gap:1px"}])
    data = base.model_dump(mode="json", by_alias=True)
    data["content"][0]["semanticId"] = "title"
    with pytest.raises(ValidationError):
        SlideSpec.model_validate(data)


def test_digest_validation_canonical_json_and_renderer_are_deterministic() -> None:
    base = _spec()
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    validate_slidespec(base)
    tampered = base.model_copy(update={"slideId": "changed"})
    with pytest.raises(SlideSpecError, match="spec.digest.mismatch"):
        validate_slidespec(tampered)
    empty = "sha256:" + "0" * 64
    request = RenderRequest(
        spec=base,
        renderer=RendererManifest(name="test", version="1", runtime="py"),
        fonts=FontManifest(digest=empty),
        assets=AssetManifest(digest=empty),
    )
    first = render_slidespec(request)
    second = render_slidespec(request)
    assert first == second
    assert 'data-semantic-id="sem:slide-1/title"' in first.html
    assert first.semanticDom[0].qid in first.html
    assert 'left:10%;position:absolute;top:10%;width:80%' in first.html
    assert 'height:720px;overflow:hidden;position:relative;width:1280px' in first.html
    assert "A &lt; B" in first.html


def test_v1_0_digest_compatibility_and_v1_1_rendered_intent() -> None:
    current = _spec()
    legacy = current.model_copy(update={"schemaUri": SLIDESPEC_SCHEMA_V1_0})
    legacy = seal_slidespec(legacy)
    validate_slidespec(legacy)
    assert legacy.schemaUri == SLIDESPEC_SCHEMA_V1_0

    leaf = current.layout.children[0].model_copy(
        update={
            "intent": LayoutIntent(
                role="decoration",
                allowIntentionalOverlap=True,
                intentionalOverlapGroup="hero-art",
            )
        }
    )
    intentional = seal_slidespec(
        current.model_copy(
            update={"layout": current.layout.model_copy(update={"children": (leaf,)})}
        )
    )
    rendered = render_slidespec(
        RenderRequest(
            spec=intentional,
            renderer=RendererManifest(name="test", version="1", runtime="py"),
            fonts=FontManifest(digest=ZERO_DIGEST),
            assets=AssetManifest(digest=ZERO_DIGEST),
        )
    )
    assert 'data-slidespec="1.1"' in rendered.html
    assert 'data-allow-overlap="true"' in rendered.html
    assert 'data-intentional-overlap-group="hero-art"' in rendered.html


@pytest.mark.parametrize(
    ("constraints", "operation", "code"),
    [
        (
            LayoutConstraints(allowedOperations=("setFontScale",)),
            {"op": "setSpacing", "target": "sem:slide-1/title", "gap": 12},
            "repair.constraint.operation_not_allowed",
        ),
        (
            LayoutConstraints(minFontScale=0.9, maxFontShrink=0.1),
            {"op": "setFontScale", "target": "sem:slide-1/title", "scale": 0.85},
            "repair.constraint.font_below_minimum",
        ),
        (
            LayoutConstraints(maxTranslation=0.02),
            {
                "op": "translateWithinParent",
                "target": "sem:slide-1/title",
                "value": {"x": 0.03, "y": 0},
            },
            "repair.constraint.translation_exceeded",
        ),
        (
            LayoutConstraints(preserveAspectRatio=True),
            {
                "op": "resizeWithinBounds",
                "target": "sem:slide-1/title",
                "width": 0.7,
                "height": 0.2,
            },
            "repair.constraint.aspect_ratio_changed",
        ),
        (
            LayoutConstraints(minWidth=0.4, minHeight=0.15),
            {
                "op": "resizeWithinBounds",
                "target": "sem:slide-1/title",
                "width": 0.3,
                "height": 0.15,
            },
            "repair.constraint.size_below_minimum",
        ),
        (
            LayoutConstraints(maxWidth=0.6, maxHeight=0.3),
            {
                "op": "resizeWithinBounds",
                "target": "sem:slide-1/title",
                "width": 0.7,
                "height": 0.2,
            },
            "repair.constraint.size_above_maximum",
        ),
    ],
)
def test_v1_1_constraints_reject_design_damaging_repairs(
    constraints: LayoutConstraints, operation: dict, code: str
) -> None:
    base = _spec()
    leaf = base.layout.children[0].model_copy(update={"constraints": constraints})
    base = seal_slidespec(
        base.model_copy(
            update={"layout": base.layout.model_copy(update={"children": (leaf,)})}
        )
    )
    with pytest.raises(SlideSpecError, match=code):
        apply_repair_plan(base, _plan(base, [operation]))


def test_acceptance_decision_keeps_inconclusive_distinct_and_hard_gated() -> None:
    base = _spec()
    common = {
        "$schema": ACCEPTANCE_SCHEMA,
        "baseSpecHash": base.digests.spec,
        "triggerType": "geometry",
        "requiredGates": ["dom"],
        "reasonCodes": ["dom.sensor_unavailable"],
    }
    decision = AcceptanceDecision.model_validate({
        **common,
        "stages": [{"stage": "dom", "status": "inconclusive",
                    "reasonCodes": ["dom.sensor_unavailable"]}],
        "outcome": "inconclusive",
    })
    assert decision.outcome == "inconclusive"
    with pytest.raises(ValidationError):
        AcceptanceDecision.model_validate({
            **common,
            "candidateSpecHash": base.digests.spec,
            "stages": [{"stage": "dom", "status": "fail",
                        "reasonCodes": ["dom.clipping"]}],
            "outcome": "accepted",
        })
