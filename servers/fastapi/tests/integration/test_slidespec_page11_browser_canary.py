"""Tracked synthetic page-11 canary using the real browser DOM probe."""

import asyncio

import pytest

from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AssetManifest,
    Bounds,
    ConnectorRoute,
    ContentNode,
    FocalPoint,
    FontManifest,
    ImportedHtmlBinding,
    ImportedHtmlSource,
    ImportedPixelBounds,
    LayoutNode,
    LayoutConstraints,
    Provenance,
    RenderRequest,
    RendererManifest,
    SlideDigests,
    SlideSpec,
)
from services.slidespec_authored_executor import (
    _dom_evidence,
    _passed_evidence,
    _qid_to_semantic,
    _repair_context,
)
from services.slidespec_repair import build_deterministic_cascade, sort_candidates
from services.slidespec_repair_pipeline import DomDefect, DomEvidence
from services.slidespec_rollout_policy import PAGE_11_SLIDE_INDEX
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityIssue,
    DomQualityReport,
    has_dom_quality_regression,
    inspect_authored_dom,
)
from utils.slide_capture import find_chrome, render_html_to_png_with_metrics
from utils.slidespec_core import (
    ZERO_DIGEST,
    apply_repair_plan,
    render_slidespec,
    seal_slidespec,
)


def _page_11_spec() -> SlideSpec:
    return seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId="synthetic-page-11",
            content=(
                ContentNode(
                    semanticId="sem:synthetic-page-11/title",
                    kind="title",
                    text="PAGE 11 TOP CLIPPING CANARY",
                ),
            ),
            layout=LayoutNode(
                layoutId="lay:synthetic-page-11/root",
                kind="canvas",
                children=(
                    LayoutNode(
                        layoutId="lay:synthetic-page-11/title",
                        kind="content",
                        contentSemanticId="sem:synthetic-page-11/title",
                        bounds=Bounds(x=0.1, y=0.02, width=0.8, height=0.03),
                    ),
                ),
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


def _request(spec: SlideSpec) -> RenderRequest:
    empty = "sha256:" + "0" * 64
    return RenderRequest(
        spec=spec,
        renderer=RendererManifest(name="slidespec", version="1", runtime="browser"),
        fonts=FontManifest(digest=empty),
        assets=AssetManifest(digest=empty),
    )


def _report(*issues: DomQualityIssue) -> DomQualityReport:
    return DomQualityReport(STYLE_REFERENCE, issues, (), len(issues), "available")


def test_authored_overlap_is_canonical_geometry_evidence_with_its_peer() -> None:
    report = _report(
        DomQualityIssue(
            "overlap", "medium", "intent is visually ambiguous", ("q-a", "q-b")
        )
    )

    evidence = _dom_evidence(report, {"q-a": "sem:a", "q-b": "sem:b"})

    assert len(evidence.defects) == 1
    assert evidence.defects[0].code == "dom.unintended_overlap"
    assert evidence.defects[0].semantic_id == "sem:a"
    assert evidence.defects[0].related_semantic_ids == ("sem:b",)
    assert evidence.telemetry_complete is True


def test_dom_evidence_deduplicates_and_keeps_the_strongest_target_defect() -> None:
    report = _report(
        DomQualityIssue("overflow", "medium", "first", ("q-title",)),
        DomQualityIssue("overflow", "high", "second", ("q-title",)),
    )

    evidence = _dom_evidence(report, {"q-title": "sem:title"})

    assert [(item.code, item.severity, item.semantic_id) for item in evidence.defects] == [
        ("dom.overflow", "high", "sem:title")
    ]


def test_imported_overflow_expands_by_measured_pixels_not_fifty_percent() -> None:
    base = _page_11_spec()
    empty = "sha256:" + "0" * 64
    title = base.layout.children[0]
    request = _request(base).model_copy(
        update={
            "source": ImportedHtmlSource(
                html='<h1 data-qid="title">PAGE 11 TOP CLIPPING CANARY</h1>',
                htmlHash=empty,
                viewportWidth=1000,
                viewportHeight=500,
                bindings=(
                    ImportedHtmlBinding(
                        semanticId="sem:synthetic-page-11/title",
                        layoutId="lay:synthetic-page-11/title",
                        qid="title",
                        tag="h1",
                        bounds=title.bounds,
                        sourceBoundsPx=ImportedPixelBounds(
                            x=100, y=10, width=800, height=15
                        ),
                        position="absolute",
                    ),
                ),
            )
        }
    )
    evidence = _dom_evidence(
        _report(DomQualityIssue("overflow", "medium", "measured", ("title",))),
        {"title": "sem:synthetic-page-11/title"},
    )

    context = _repair_context(
        request,
        evidence,
        {
            "elements": [
                {
                    "qid": "title",
                    "clientWidth": 800,
                    "scrollWidth": 800,
                    "clientHeight": 15,
                    "scrollHeight": 17,
                }
            ]
        },
    )

    repaired = context["containerBounds"][0]["bounds"]
    assert repaired["height"] == pytest.approx(title.bounds.height + 6 / 500)
    assert repaired["height"] < title.bounds.height * 1.5


def test_overlap_has_no_unsafe_common_ancestor_resize_candidate() -> None:
    context = _repair_context(
        _page_11_spec(),
        _dom_evidence(
            _report(
                DomQualityIssue(
                    "overlap", "medium", "unsafe", ("title", "peer")
                )
            ),
            {
                "title": "sem:synthetic-page-11/title",
                "peer": "sem:peer",
            },
        ),
    )

    assert context["containerBounds"] == []
    assert context["fontScale"] == []


def test_acceptance_counts_only_new_dom_defects_not_preserved_baseline_findings() -> None:
    preserved = DomQualityIssue("contrast", "medium", "existing", ("q-title",))
    evidence = _passed_evidence(
        baseline_dom=_report(preserved),
        candidate_dom=_report(preserved),
        ocr_non_regression=True,
        token_recall=1.0,
        vqa_non_regression=True,
        vqa_improved=True,
        target_resolved=True,
    )

    assert evidence.dom.new_high_medium_defects == 0
    assert evidence.dom.non_regression is True


async def _inspect(request: RenderRequest):
    rendered = render_slidespec(request)
    png, metrics = await render_html_to_png_with_metrics(rendered.html)
    return png, inspect_authored_dom(rendered.html, metrics)


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_real_browser_page_11_clipping_is_repaired_without_regression() -> None:
    assert PAGE_11_SLIDE_INDEX == 10
    base = _page_11_spec()
    request = _request(base)
    baseline_png, baseline = asyncio.run(_inspect(request))
    evidence = _dom_evidence(baseline, _qid_to_semantic(request))

    assert baseline_png.startswith(b"\x89PNG")
    assert evidence.required_geometry_defect

    plans = sort_candidates(build_deterministic_cascade(_repair_context(base, evidence)))
    accepted = None
    for plan in plans:
        applied = apply_repair_plan(base, plan)
        candidate_request = request.model_copy(update={"spec": applied.spec})
        candidate_png, candidate = asyncio.run(_inspect(candidate_request))
        candidate_evidence = _dom_evidence(
            candidate, _qid_to_semantic(candidate_request)
        )
        if not candidate_evidence.hard_failure:
            accepted = (applied, candidate_png, candidate)
            break

    assert accepted is not None
    applied, candidate_png, candidate = accepted
    assert candidate_png.startswith(b"\x89PNG")
    assert applied.spec.digests.content == base.digests.content
    assert applied.spec.digests.importantTokens == base.digests.importantTokens
    assert not has_dom_quality_regression(baseline, candidate)


@pytest.mark.skipif(find_chrome() is None, reason="headless Chrome is required")
def test_native_connector_reroute_uses_chrome_endpoints_and_avoids_obstacle() -> None:
    base = _page_11_spec()
    connector_semantic = "sem:synthetic-page-11/connector"
    obstacle_semantic = "sem:synthetic-page-11/obstacle"
    spec = seal_slidespec(
        base.model_copy(
            update={
                "content": (
                    ContentNode(
                        semanticId=connector_semantic,
                        kind="connector",
                        payload={},
                        readingOrder=0,
                    ),
                    ContentNode(
                        semanticId=obstacle_semantic,
                        kind="shape",
                        payload={"label": "anonymized obstacle"},
                        readingOrder=1,
                    ),
                ),
                "layout": LayoutNode(
                    layoutId="lay:synthetic-page-11/root",
                    kind="canvas",
                    children=(
                        LayoutNode(
                            layoutId="lay:synthetic-page-11/connector",
                            kind="content",
                            contentSemanticId=connector_semantic,
                            bounds=Bounds(x=0.1, y=0.2, width=0.8, height=0.6),
                            connectorRoute=ConnectorRoute(
                                points=(
                                    FocalPoint(x=0, y=0.5),
                                    FocalPoint(x=1, y=0.5),
                                )
                            ),
                            constraints=LayoutConstraints(
                                allowedOperations=("rerouteConnector",)
                            ),
                        ),
                        LayoutNode(
                            layoutId="lay:synthetic-page-11/obstacle",
                            kind="content",
                            contentSemanticId=obstacle_semantic,
                            bounds=Bounds(x=0.45, y=0.4, width=0.1, height=0.2),
                        ),
                    ),
                ),
            }
        )
    )
    request = _request(spec)
    rendered = render_slidespec(request)
    png, metrics = asyncio.run(render_html_to_png_with_metrics(rendered.html))
    assert png.startswith(b"\x89PNG")
    context = _repair_context(
        request,
        DomEvidence(
            (
                DomDefect(
                    "constraint.non_overlap.violated",
                    "high",
                    connector_semantic,
                    (obstacle_semantic,),
                ),
            )
        ),
        metrics,
    )

    connector_context = context["connectorRoute"][0]
    assert connector_context["measurement"]["telemetryComplete"] is True
    assert connector_context["measurement"]["obstacleFree"] is True
    assert connector_context["measurement"]["minimumClearancePx"] >= 1
    assert connector_context["measurement"]["endpointAnchors"]["maxErrorPx"] <= 0.5
    plans = build_deterministic_cascade(context)
    reroute = next(
        plan for plan in plans if plan.operations[0].op == "rerouteConnector"
    )
    assert reroute.operations[0].value.points[0] == FocalPoint(x=0, y=0.5)
    assert reroute.operations[0].value.points[-1] == FocalPoint(x=1, y=0.5)
    assert len(reroute.operations[0].value.points) == 4

    repaired = apply_repair_plan(spec, reroute).spec
    rerendered = render_slidespec(request.model_copy(update={"spec": repaired}))
    _, repaired_metrics = asyncio.run(
        render_html_to_png_with_metrics(rerendered.html)
    )
    connector_qid = next(
        entry.qid
        for entry in rerendered.semanticDom
        if entry.semanticId == connector_semantic
    )
    repaired_metric = next(
        item for item in repaired_metrics["elements"] if item["qid"] == connector_qid
    )
    assert repaired_metric["connector_geometry"]["measured"] is True
    assert len(repaired_metric["connector_geometry"]["pointsPx"]) == 4
