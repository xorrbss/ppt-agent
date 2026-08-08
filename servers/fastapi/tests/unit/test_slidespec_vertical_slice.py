"""Operational synthetic page-11 canary for the SlideSpec-first vertical slice."""

import asyncio
import uuid

from models.slidespec_v1 import (
    REPAIR_PLAN_SCHEMA,
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    ImportantToken,
    LayoutNode,
    Provenance,
    RenderRequest,
    RendererManifest,
    RepairPlan,
    SlideDigests,
    SlideSpec,
)
from services.slidespec_repair_pipeline import (
    DomDefect,
    DomEvidence,
    PublicationReceipt,
    RenderedCandidate,
    SensorResult,
    SlideSpecInput,
    SlideSpecRepairDependencies,
    SlideSpecRepairPipeline,
)
from services.slidespec_rollout_policy import resolve_slidespec_repair_rollout
from utils.slidespec_core import (
    ZERO_DIGEST,
    apply_repair_plan,
    render_slidespec,
    seal_slidespec,
)


def _top_clipped_page_11() -> SlideSpec:
    return seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=17,
            slideId="synthetic-page-11",
            content=(
                ContentNode(
                    semanticId="sem:page-11/title",
                    kind="title",
                    text="5개월 조기완료 전략",
                    importantTokens=("duration", "strategy"),
                ),
            ),
            layout=LayoutNode(
                layoutId="lay:page-11/root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId="lay:page-11/title",
                        kind="content",
                        contentSemanticId="sem:page-11/title",
                        bounds=Bounds(x=0.08, y=0, width=0.84, height=0.12),
                    ),
                ),
            ),
            tokens=(
                ImportantToken(name="duration", value="5개월"),
                ImportantToken(name="strategy", value="조기완료"),
            ),
            provenance=Provenance(source="native"),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )


def _repair(base: SlideSpec) -> RepairPlan:
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": "rp-page-11-top-safe-area",
            "slideId": base.slideId,
            "baseSpecRevision": base.specRevision,
            "baseSpecHash": base.digests.spec,
            "expectedContentDigest": base.digests.content,
            "scope": ["sem:page-11/title"],
            "diagnosticRefs": ["dom.clipping.top"],
            "strategy": "deterministic_cascade",
            "operations": [
                {
                    "op": "setBounds",
                    "target": "sem:page-11/title",
                    "bounds": {"x": 0.08, "y": 0.05, "width": 0.84, "height": 0.12},
                    "precondition": {
                        "currentBounds": {
                            "x": 0.08,
                            "y": 0,
                            "width": 0.84,
                            "height": 0.12,
                        }
                    },
                }
            ],
            "limits": {
                "maxOperations": 1,
                "maxAffectedBlocks": 1,
                "maxLayoutDepth": 4,
            },
            "proposedBy": {
                "kind": "rule_engine",
                "policyVersion": "page-11-canary/1.0",
                "costVersion": "repair-cost/1.0",
            },
        }
    )


def test_page_11_top_clipping_repairs_without_llm_and_publishes_verified(monkeypatch):
    base = _top_clipped_page_11()
    plan = _repair(base)
    adapted = SlideSpecInput(
        revision_id=str(base.specRevision),
        spec_hash=base.digests.spec,
        semantic_text={node.semanticId: node.text or "" for node in base.content},
        important_tokens=tuple(str(token.value) for token in base.tokens if token.important),
    )
    state = {}
    calls = {"llm": 0, "ocr": 0, "vqa": 0, "publish": 0}

    async def cascade(spec, dom):
        return [plan]

    async def propose(spec, dom):
        calls["llm"] += 1
        return []

    async def validate(spec, candidate_plan):
        applied = apply_repair_plan(base, candidate_plan)
        state["candidate"] = applied.spec
        state["touched"] = applied.touchedLayoutIds
        return SensorResult(True)

    async def render(spec, candidate_plan):
        rendered = render_slidespec(
            RenderRequest(
                spec=state["candidate"],
                renderer=RendererManifest(name="canary", version="1", runtime="python"),
                fonts=FontManifest(digest=ZERO_DIGEST),
                assets=AssetManifest(digest=ZERO_DIGEST),
            )
        )
        state["rendered"] = rendered
        return RenderedCandidate(rendered.htmlHash, rendered.semanticDomHash)

    async def inspect_dom(artifact):
        title = next(
            item
            for item in state["rendered"].semanticDom
            if item.semanticId == "sem:page-11/title"
        )
        if title.bounds is None or title.bounds.y < 0.05:
            return DomEvidence(
                (DomDefect("dom.clipping", "high", "sem:page-11/title"),)
            )
        return DomEvidence()

    async def ocr(artifact, inventory):
        calls["ocr"] += 1
        assert inventory.text_by_semantic_id == {
            "sem:page-11/title": "5개월 조기완료 전략"
        }
        assert inventory.important_tokens == ("5개월", "조기완료")
        return SensorResult(True, cache_hit=False)

    async def vqa(artifact):
        calls["vqa"] += 1
        return SensorResult(True, cache_hit=False, quality_gain=0.75)

    async def publish(spec, candidate_plan, artifact):
        calls["publish"] += 1
        candidate = state["candidate"]
        rendered = state["rendered"]
        hashes_match = (
            artifact.html_hash == rendered.htmlHash
            and artifact.render_hash == rendered.semanticDomHash
            and candidate.digests.content == base.digests.content
            and candidate.digests.hierarchy == base.digests.hierarchy
            and candidate.digests.importantTokens == base.digests.importantTokens
        )
        return PublicationReceipt(True, True, hashes_match, "page-11-receipt")

    monkeypatch.setenv("SLIDESPEC_REPAIR_CANARY", "100")
    monkeypatch.setenv("SLIDESPEC_REPAIR_APPLY", "1")
    rollout = resolve_slidespec_repair_rollout(
        uuid.UUID("00000000-0000-0000-0000-000000000011"),
        slide_index=10,
        opt_in=True,
        owner_id="synthetic",
    )
    result = asyncio.run(
        SlideSpecRepairPipeline(
            SlideSpecRepairDependencies(
                deterministic_cascade=cascade,
                propose=propose,
                validate_plan=validate,
                render_plan=render,
                inspect_dom=inspect_dom,
                run_ocr=ocr,
                run_vqa=vqa,
                publish=publish,
            )
        ).run(spec=adapted, baseline_dom=DomEvidence((
            DomDefect("dom.clipping", "high", "sem:page-11/title"),
        )), rollout=rollout)
    )

    candidate = state["candidate"]
    assert result.trigger == "geometry"
    assert result.outcomes[0].applied is True
    assert result.funnel.metrics()["applied"] == 1
    assert calls == {"llm": 0, "ocr": 1, "vqa": 1, "publish": 1}
    assert state["touched"] == ("lay:page-11/title",)
    assert candidate.layout.children[0].bounds.y == 0.05
    assert candidate.digests.content == base.digests.content
    assert candidate.digests.hierarchy == base.digests.hierarchy
    assert candidate.digests.importantTokens == base.digests.importantTokens
