from __future__ import annotations

import json
import re

import pytest

from models.slidespec_v1 import (
    REPAIR_PLAN_SCHEMA,
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ContentNode,
    FontManifest,
    ImportedHtmlBinding,
    ImportedHtmlSource,
    ImportedPixelBounds,
    Insets,
    LayoutConstraints,
    LayoutNode,
    Provenance,
    RepairPlan,
    RenderRequest,
    RendererManifest,
    SlideDigests,
    SlideSpec,
)
from utils.llm_calls.propose_slidespec_repair import build_messages
from utils.slidespec_core import (
    ZERO_DIGEST,
    SlideSpecError,
    apply_repair_plan,
    digest,
    render_slidespec,
    seal_slidespec,
)
from utils.slidespec_constraints import imported_projection_roundtrip_errors
from utils.slidespec_projection import (
    PROJECTION_CAPABILITY_VERSION,
    dry_project_plan,
    projection_schema_context,
)


EMPTY_DIGEST = "sha256:" + "0" * 64


def _fixture_request(
    page: int,
    *,
    parent_display: str = "block",
    child_position: str = "static",
    child_static_safe: bool = False,
    child_anchors: bool = False,
    child_trailing_anchors: bool = False,
    child_binding: bool = True,
) -> RenderRequest:
    parent_semantic = f"sem:page-{page}/container"
    child_semantic = f"sem:page-{page}/copy"
    parent_layout = f"lay:page-{page}/container"
    child_layout = f"lay:page-{page}/copy"
    parent_bounds = Bounds(x=0.08, y=0.08, width=0.84, height=0.76)
    child_bounds = Bounds(x=0.14, y=0.18, width=0.48, height=0.18)
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=2,
            slideId=f"page-{page}",
            content=(
                ContentNode(
                    semanticId=parent_semantic,
                    kind="text",
                    text=f"projection fixture {page}",
                    readingOrder=0,
                ),
                ContentNode(
                    semanticId=child_semantic,
                    parentSemanticId=parent_semantic,
                    kind="text",
                    text="bounded copy",
                    readingOrder=1,
                ),
            ),
            layout=LayoutNode(
                layoutId=f"lay:page-{page}/root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId=parent_layout,
                        kind="content",
                        contentSemanticId=parent_semantic,
                        bounds=parent_bounds,
                        spacing=12,
                        padding=Insets(top=10 / 547.2, right=12 / 1075.2, bottom=10 / 547.2, left=12 / 1075.2),
                        columnRatio=(1.0, 1.0) if "grid" in parent_display else (),
                        constraints=LayoutConstraints(
                            allowedOperations=(
                                "setSpacing",
                                "setPadding",
                                "setColumnRatio",
                                "setBounds",
                                "resizeWithinBounds",
                            )
                        ),
                    ),
                    LayoutNode(
                        layoutId=child_layout,
                        kind="content",
                        contentSemanticId=child_semantic,
                        bounds=child_bounds,
                        constraints=LayoutConstraints(
                            allowedOperations=(
                                "setBounds",
                                "translateWithinParent",
                                "resizeWithinBounds",
                                "setFontScale",
                                "setTextFitPolicy",
                            )
                        ),
                    ),
                ),
            ),
            provenance=Provenance(source="authored-html-import"),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    parent_qid = f"p{page}-container"
    child_qid = f"p{page}-copy"
    html = (
        "<!doctype html><html><body>"
        f'<section data-qid="{parent_qid}">'
        f'<div data-qid="{child_qid}">bounded copy</div>'
        "</section></body></html>"
    )
    bindings = [
        ImportedHtmlBinding(
            semanticId=parent_semantic,
            layoutId=parent_layout,
            qid=parent_qid,
            tag="section",
            bounds=parent_bounds,
            sourceBoundsPx=ImportedPixelBounds(
                x=102.4, y=57.6, width=1075.2, height=547.2
            ),
            position="static",
            transform="none",
            display=parent_display,
            gapPx=12,
            paddingTopPx=10,
            paddingRightPx=12,
            paddingBottomPx=10,
            paddingLeftPx=12,
            gridTemplateColumns="1fr 1fr" if "grid" in parent_display else "none",
            columnRatio=(1.0, 1.0) if "grid" in parent_display else (),
            objectFit="contain",
        )
    ]
    if child_binding:
        bindings.append(
            ImportedHtmlBinding(
                semanticId=child_semantic,
                layoutId=child_layout,
                qid=child_qid,
                tag="div",
                bounds=child_bounds,
                sourceBoundsPx=ImportedPixelBounds(
                    x=179.2, y=129.6, width=614.4, height=129.6
                ),
                fontSizePx=24,
                position=child_position,
                transform="none",
                staticTranslationSafe=child_static_safe,
                leftPx=179.2 if child_anchors else None,
                topPx=129.6 if child_anchors else None,
                rightPx=100 if child_trailing_anchors else None,
                bottomPx=80 if child_trailing_anchors else None,
                display="block",
                objectFit="contain",
            )
        )
    return RenderRequest(
        spec=spec,
        renderer=RendererManifest(name="slidespec-html", version="1", runtime="browser"),
        fonts=FontManifest(digest=EMPTY_DIGEST),
        assets=AssetManifest(digest=EMPTY_DIGEST),
        source=ImportedHtmlSource(
            html=html,
            htmlHash=digest(html),
            viewportWidth=1280,
            viewportHeight=720,
            bindings=tuple(bindings),
            importerVersion="authored-html-import/4",
        ),
    )


def _plan(request: RenderRequest, page: int, operation: dict[str, object]) -> RepairPlan:
    return RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": f"projection-page-{page}",
            "slideId": request.spec.slideId,
            "baseSpecRevision": request.spec.specRevision,
            "baseSpecHash": request.spec.digests.spec,
            "expectedContentDigest": request.spec.digests.content,
            "scope": [operation["target"]],
            "strategy": "minimal_change",
            "operations": [operation],
            "proposedBy": {
                "kind": "rule_engine",
                "policyVersion": "projection-fixture/1",
            },
        }
    )


@pytest.mark.parametrize(
    ("page", "request_kwargs", "operation", "proof"),
    (
        (
            28,
            {"parent_display": "flex"},
            {"op": "setSpacing", "target": "lay:page-28/container", "gap": 18},
            "computed-style:gap",
        ),
        (
            38,
            {"parent_display": "grid"},
            {
                "op": "setColumnRatio",
                "target": "lay:page-38/container",
                "value": [1.3, 0.7],
            },
            "computed-style:grid-template-columns",
        ),
        (
            45,
            {"parent_display": "flex"},
            {
                "op": "setPadding",
                "target": "lay:page-45/container",
                "padding": {
                    "top": 0.03,
                    "right": 0.02,
                    "bottom": 0.03,
                    "left": 0.02,
                },
            },
            "computed-style:padding-top",
        ),
        (
            50,
            {},
            {
                "op": "resizeWithinBounds",
                "target": "lay:page-50/container",
                "width": 0.88,
                "height": 0.80,
            },
            "browser-bounds:size",
        ),
        (
            57,
            {"child_position": "absolute", "child_anchors": True},
            {
                "op": "translateWithinParent",
                "target": "lay:page-57/copy",
                "value": {"x": 0.02, "y": 0.01},
            },
            "computed-style:position-anchor",
        ),
        (
            76,
            {"child_static_safe": True},
            {
                "op": "translateWithinParent",
                "target": "lay:page-76/copy",
                "value": {"x": -0.02, "y": 0.01},
            },
            "computed-style:transform",
        ),
    ),
)
def test_review_projection_fixtures_are_dry_planned_and_renderable(
    page: int,
    request_kwargs: dict[str, object],
    operation: dict[str, object],
    proof: str,
) -> None:
    request = _fixture_request(page, **request_kwargs)
    plan = _plan(request, page, operation)

    projection = dry_project_plan(request, plan)

    assert projection.supported
    assert proof in projection.capabilities[0].proof
    applied = apply_repair_plan(request.spec, plan)
    rendered = render_slidespec(request.model_copy(update={"spec": applied.spec}))
    assert rendered.htmlHash != request.source.htmlHash


def test_unsupported_position_is_rejected_before_source_patch() -> None:
    request = _fixture_request(76)
    plan = _plan(
        request,
        76,
        {
            "op": "translateWithinParent",
            "target": "lay:page-76/copy",
            "value": {"x": 0.01, "y": 0.01},
        },
    )

    projection = dry_project_plan(request, plan)

    assert projection.reason_code == "render.source_projection.position_unsupported"
    applied = apply_repair_plan(request.spec, plan)
    with pytest.raises(SlideSpecError) as error:
        render_slidespec(request.model_copy(update={"spec": applied.spec}))
    assert error.value.code == projection.reason_code


def test_missing_binding_and_unsupported_gap_have_precise_reasons() -> None:
    missing = _fixture_request(28, parent_display="flex", child_binding=False)
    missing_plan = _plan(
        missing,
        28,
        {"op": "setFontScale", "target": "lay:page-28/copy", "scale": 0.95},
    )
    block = _fixture_request(38)
    gap_plan = _plan(
        block,
        38,
        {"op": "setSpacing", "target": "lay:page-38/container", "gap": 18},
    )

    assert dry_project_plan(missing, missing_plan).reason_code == "render.source_binding.invalid"
    assert dry_project_plan(block, gap_plan).reason_code == "render.source_projection.gap_unsupported"


def test_projection_context_is_closed_target_scoped_and_in_llm_prompt() -> None:
    request = _fixture_request(57, child_position="absolute", child_anchors=True)
    context = projection_schema_context(
        request, target_semantic_ids=["sem:page-57/copy"]
    )

    assert context["version"] == PROJECTION_CAPABILITY_VERSION
    assert [item["semanticId"] for item in context["targets"]] == ["sem:page-57/copy"]
    assert "translateWithinParent" in context["supportedOperations"]
    assert "rerouteConnector" not in context["supportedOperations"]

    messages, _ = build_messages(
        request.spec.model_dump(mode="json", by_alias=True),
        [{"code": "dom.off_canvas", "semanticId": "sem:page-57/copy"}],
        slide_id=request.spec.slideId,
        base_spec_revision=request.spec.specRevision,
        base_spec_hash=request.spec.digests.spec,
        expected_content_digest=request.spec.digests.content,
        projection_context=context,
    )
    prompt = str(messages[-1].content)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["projectionCapability"] == context


def test_text_fit_shrink_is_blocked_by_shared_renderer_capability() -> None:
    request = _fixture_request(45)
    plan = _plan(
        request,
        45,
        {
            "op": "setTextFitPolicy",
            "target": "lay:page-45/copy",
            "value": "shrink",
        },
    )

    projection = dry_project_plan(request, plan)

    assert projection.reason_code == "render.source_projection.text_fit_unsupported"
    applied = apply_repair_plan(request.spec, plan)
    with pytest.raises(SlideSpecError) as error:
        render_slidespec(request.model_copy(update={"spec": applied.spec}))
    assert error.value.code == projection.reason_code


def _rendered_tag(html: str, qid: str) -> str:
    match = re.search(
        rf'<[^>]+data-qid="{re.escape(qid)}"[^>]*>', html, flags=re.IGNORECASE
    )
    assert match is not None
    return match.group(0)


def test_dual_position_anchors_move_together_without_resizing_the_element() -> None:
    request = _fixture_request(
        28,
        child_position="absolute",
        child_anchors=True,
        child_trailing_anchors=True,
    )
    plan = _plan(
        request,
        28,
        {
            "op": "translateWithinParent",
            "target": "lay:page-28/copy",
            "value": {"x": 0.02, "y": 0.01},
        },
    )

    applied = apply_repair_plan(request.spec, plan)
    rendered = render_slidespec(request.model_copy(update={"spec": applied.spec}))
    tag = _rendered_tag(rendered.html, "p28-copy")

    assert "left: 204.8000px" in tag
    assert "right: 74.4000px" in tag
    assert "top: 136.8000px" in tag
    assert "bottom: 72.8000px" in tag


def test_positioned_resize_pins_origin_and_rebalances_trailing_anchors() -> None:
    request = _fixture_request(
        50,
        child_position="absolute",
        child_anchors=True,
        child_trailing_anchors=True,
    )
    plan = _plan(
        request,
        50,
        {
            "op": "resizeWithinBounds",
            "target": "lay:page-50/copy",
            "width": 0.52,
            "height": 0.22,
        },
    )

    projection = dry_project_plan(request, plan)
    applied = apply_repair_plan(request.spec, plan)
    rendered = render_slidespec(request.model_copy(update={"spec": applied.spec}))
    tag = _rendered_tag(rendered.html, "p50-copy")

    assert projection.supported
    assert "computed-style:position-anchor" in projection.capabilities[0].proof
    assert "left: 179.2000px" in tag
    assert "right: 48.8000px" in tag
    assert "top: 129.6000px" in tag
    assert "bottom: 51.2000px" in tag


@pytest.mark.parametrize("page", (45, 76))
def test_roundtrip_padding_uses_unclipped_imported_source_box(page: int) -> None:
    request = _fixture_request(page)
    parent = request.spec.layout.children[0]
    clipped_bounds = Bounds(x=0.08, y=0.96, width=0.84, height=0.04)
    source_height = 62.0
    parent = parent.model_copy(
        update={
            "bounds": clipped_bounds,
            "padding": Insets(
                top=10 / source_height,
                right=12 / 1075.2,
                bottom=10 / source_height,
                left=12 / 1075.2,
            ),
        }
    )
    layout = request.spec.layout.model_copy(
        update={"children": (parent, request.spec.layout.children[1])}
    )
    spec = request.spec.model_copy(update={"layout": layout})
    parent_binding = request.source.bindings[0].model_copy(
        update={
            "bounds": clipped_bounds,
            "sourceBoundsPx": ImportedPixelBounds(
                x=102.4, y=691.2, width=1075.2, height=source_height
            ),
        }
    )
    source = request.source.model_copy(
        update={"bindings": (parent_binding, request.source.bindings[1])}
    )
    candidate = request.model_copy(update={"spec": spec, "source": source})
    metrics = {
        "width": 1280,
        "height": 720,
        "elements": [
            {
                "qid": binding.qid,
                "bbox": binding.sourceBoundsPx.model_dump(mode="json"),
                "computed_style": {
                    "paddingTop": f"{binding.paddingTopPx:g}px",
                    "paddingRight": f"{binding.paddingRightPx:g}px",
                    "paddingBottom": f"{binding.paddingBottomPx:g}px",
                    "paddingLeft": f"{binding.paddingLeftPx:g}px",
                },
            }
            for binding in source.bindings
        ],
    }

    assert imported_projection_roundtrip_errors(candidate, metrics) == ()
