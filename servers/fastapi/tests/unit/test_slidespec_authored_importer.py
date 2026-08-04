import asyncio
from types import SimpleNamespace
import uuid

import pytest

from models.slidespec_v1 import (
    REPAIR_PLAN_SCHEMA,
    ConnectorRoute,
    FocalPoint,
    GridPlacement,
    ImportantToken,
    Insets,
    RenderRequest,
    RepairPlan,
)
from models.sql.slide import SlideModel
from services.slidespec_authored_importer import (
    SlideSpecImportError,
    build_authored_import_request,
    ensure_authored_slidespec_imports,
)
from services.slidespec_artifact_store import SlideSpecArtifactError
from utils.slidespec_core import (
    SlideSpecError,
    apply_repair_plan,
    render_slidespec,
    seal_slidespec,
)
from utils.slidespec_constraints import (
    imported_projection_roundtrip_errors,
    observations_from_browser_metrics,
)


def _slide(html: str) -> SlideModel:
    return SlideModel(
        id=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        presentation=uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        layout_group="authored",
        layout="authored",
        index=0,
        content={},
        html_content=html,
        properties={},
    )


def _metrics() -> dict:
    return {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "root",
                "bbox": {"x": 0, "y": 0, "width": 1280, "height": 720},
                "client_width": 1280,
                "client_height": 720,
                "scroll_width": 1280,
                "scroll_height": 720,
                "text": "Hello",
                "computed_style": {"position": "relative"},
            },
            {
                "qid": "title",
                "parent_qid": "root",
                "bbox": {"x": 100, "y": 50, "width": 400, "height": 60},
                "client_width": 400,
                "client_height": 60,
                "scroll_width": 400,
                "scroll_height": 60,
                "font_size": 32,
                "text": "Hello",
                "computed_style": {
                    "position": "absolute",
                    "left": "100px",
                    "top": "50px",
                    "fontSize": "32px",
                    "fontFamily": "Arial",
                    "lineHeight": "40px",
                    "color": "rgb(0, 0, 0)",
                    "backgroundColor": "rgb(255, 255, 255)",
                    "display": "block",
                    "visibility": "visible",
                    "opacity": "1",
                    "zIndex": "7",
                },
            },
            # Legacy documents can have qids on non-editable void tags.  They
            # must not make otherwise complete editable telemetry fail.
            {
                "qid": "legacy-break",
                "bbox": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
        ],
    }


def test_import_baseline_is_lossless_and_candidate_projects_minimal_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<!doctype html><html><body><section data-qid="root">'
        '<h1 data-qid="title" style="position:absolute;left:100px;top:50px">'
        'Hello</h1><br data-qid="legacy-break"></section></body></html>'
    )

    async def fake_render(source: str) -> tuple[bytes, dict]:
        assert source == html
        return b"png", _metrics()

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )
    request, image, evidence = asyncio.run(build_authored_import_request(_slide(html)))

    baseline = render_slidespec(request)
    assert image == b"png"
    assert baseline.html == html
    assert request.source is not None
    title_layout = next(
        binding.layoutId for binding in request.source.bindings if binding.qid == "title"
    )
    browser_observations = observations_from_browser_metrics(request, _metrics())
    observed = browser_observations[title_layout]
    canvas_observation = browser_observations[request.spec.layout.layoutId]
    assert (
        canvas_observation.bounds.x,
        canvas_observation.bounds.y,
        canvas_observation.bounds.width,
        canvas_observation.bounds.height,
    ) == (0.0, 0.0, 1.0, 1.0)
    metrics_without_viewport = _metrics()
    metrics_without_viewport.pop("width")
    metrics_without_viewport.pop("height")
    assert request.spec.layout.layoutId not in observations_from_browser_metrics(
        request, metrics_without_viewport
    )
    assert (
        observed.bounds.x,
        observed.bounds.y,
        observed.bounds.width,
        observed.bounds.height,
    ) == pytest.approx((100 / 1280, 50 / 720, 400 / 1280, 60 / 720))
    assert observed.font_size_px == 32
    assert observed.line_height_ratio == 1.25
    assert observed.contrast_ratio == 21
    assert observed.visible is True
    assert observed.truncated is False
    assert observed.z_index == 7
    assert request.source is not None
    assert evidence["render_parity"] == "byte_identical_baseline"
    assert {binding.qid for binding in request.source.bindings} == {"root", "title"}

    title = next(
        content for content in request.spec.content if content.payload["sourceQid"] == "title"
    )
    layout = next(
        child
        for child in request.spec.layout.children
        if child.contentSemanticId == title.semanticId
    )
    # Keep the fixture inside the imported node's conservative translation
    # budget; larger moves now require an explicit layout-intent override.
    new_bounds = layout.bounds.model_copy(update={"x": layout.bounds.x + 0.05})
    plan = RepairPlan.model_validate(
        {
            "$schema": REPAIR_PLAN_SCHEMA,
            "planId": "rp-imported-title",
            "slideId": request.spec.slideId,
            "baseSpecRevision": request.spec.specRevision,
            "baseSpecHash": request.spec.digests.spec,
            "expectedContentDigest": request.spec.digests.content,
            "scope": [title.semanticId],
            "diagnosticRefs": ["dom.off_canvas.title"],
            "strategy": "minimal_change",
            "operations": [
                {
                    "op": "setBounds",
                    "target": title.semanticId,
                    "bounds": new_bounds.model_dump(mode="json"),
                }
            ],
            "limits": {"maxOperations": 1, "maxAffectedBlocks": 1},
            "proposedBy": {
                "kind": "rule_engine",
                "policyVersion": "repair-cascade/1.0",
            },
        }
    )
    candidate_spec = apply_repair_plan(request.spec, plan).spec
    candidate = render_slidespec(request.model_copy(update={"spec": candidate_spec}))

    assert candidate.html != html
    assert "Hello" in candidate.html
    assert f"left: {new_bounds.x * 1280:.4f}px" in candidate.html
    assert "top: 50px" in candidate.html


def test_importer_v3_infers_all_relational_constraint_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<html><body><section data-qid="root">'
        '<h1 data-qid="title">Title</h1>'
        '<p data-qid="body">Body</p>'
        '</section></body></html>'
    )
    metrics = _metrics()
    metrics["elements"] = [
        item for item in metrics["elements"] if item["qid"] != "legacy-break"
    ]
    metrics["elements"].append(
        {
            "qid": "body",
            "parent_qid": "root",
            "bbox": {"x": 600, "y": 50, "width": 300, "height": 60},
            "client_width": 300,
            "client_height": 60,
            "scroll_width": 300,
            "scroll_height": 60,
            "font_size": 20,
            "text": "Body",
            "computed_style": {
                "position": "absolute",
                "left": "600px",
                "top": "50px",
                "fontSize": "20px",
                "fontFamily": "Arial",
            },
        }
    )

    async def fake_render(_source: str) -> tuple[bytes, dict]:
        return b"png", metrics

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )

    request, _image, evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )

    assert request.source is not None
    assert request.source.importerVersion == "authored-html-import/3"
    assert request.spec.provenance.adapterVersion == "authored-html-import/3"
    assert {constraint.kind for constraint in request.spec.constraints} == {
        "group",
        "anchor",
        "alignment",
        "gap",
        "containment",
        "nonOverlap",
        "order",
        "readability",
    }
    assert evidence["constraint_count"] == len(request.spec.constraints)
    for layout in request.spec.layout.children:
        assert layout.bounds is not None
        assert layout.constraints is not None
        assert layout.constraints.minWidth == pytest.approx(layout.bounds.width * 0.5)
        assert layout.constraints.minHeight == pytest.approx(layout.bounds.height * 0.5)
        assert layout.constraints.maxWidth == pytest.approx(
            min(1, layout.bounds.width + 0.25)
        )
        assert layout.constraints.maxHeight == pytest.approx(
            min(1, layout.bounds.height + 0.25)
        )


def test_projection_covers_grid_text_fit_image_crop_and_browser_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<html><body><section data-qid="grid">'
        '<p data-qid="copy">Copy</p>'
        '<img data-qid="photo" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">'
        '</section></body></html>'
    )
    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "grid",
                "bbox": {"x": 100, "y": 100, "width": 600, "height": 300},
                "client_width": 600,
                "client_height": 300,
                "scroll_width": 600,
                "scroll_height": 300,
                "text": "",
                "computed_style": {
                    "position": "absolute",
                    "left": "100px",
                    "top": "100px",
                    "display": "grid",
                    "gap": "10px",
                    "paddingTop": "10px",
                    "paddingRight": "10px",
                    "paddingBottom": "10px",
                    "paddingLeft": "10px",
                    "alignItems": "stretch",
                    "justifyContent": "start",
                    "gridTemplateColumns": "200px 400px",
                },
            },
            {
                "qid": "copy",
                "parent_qid": "grid",
                "bbox": {"x": 120, "y": 120, "width": 200, "height": 80},
                "client_width": 200,
                "client_height": 80,
                "scroll_width": 200,
                "scroll_height": 80,
                "font_size": 20,
                "text": "Copy",
                "computed_style": {
                    "position": "relative",
                    "left": "0px",
                    "top": "0px",
                    "display": "block",
                    "fontSize": "20px",
                    "fontFamily": "Arial",
                    "lineHeight": "24px",
                    "gridColumn": "1 / span 1",
                    "gridRow": "1 / span 1",
                    "whiteSpace": "normal",
                    "overflow": "visible",
                    "overflowWrap": "normal",
                    "textOverflow": "clip",
                },
            },
            {
                "qid": "photo",
                "parent_qid": "grid",
                "bbox": {"x": 340, "y": 120, "width": 300, "height": 180},
                "client_width": 300,
                "client_height": 180,
                "scroll_width": 300,
                "scroll_height": 180,
                "text": "",
                "computed_style": {
                    "position": "relative",
                    "left": "0px",
                    "top": "0px",
                    "display": "block",
                    "gridColumn": "2 / span 1",
                    "gridRow": "1 / span 1",
                    "objectFit": "contain",
                    "objectPosition": "50% 50%",
                },
            },
        ],
    }

    async def fake_render(_source: str) -> tuple[bytes, dict]:
        return b"png", metrics

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )
    request, _image, _evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )
    assert request.source is not None
    by_qid = {binding.qid: binding for binding in request.source.bindings}
    by_layout = {node.layoutId: node for node in request.spec.layout.children}
    grid = by_layout[by_qid["grid"].layoutId]
    copy = by_layout[by_qid["copy"].layoutId]
    photo = by_layout[by_qid["photo"].layoutId]
    assert copy.gridPlacement == GridPlacement(
        row=0, column=0, rowSpan=1, columnSpan=1
    )
    assert copy.constraints is not None
    assert "setGridPlacement" in copy.constraints.allowedOperations

    updated = {
        grid.layoutId: grid.model_copy(
            update={
                "spacing": 18,
                "padding": Insets(top=0.1, right=0.05, bottom=0.1, left=0.05),
                "alignment": "center",
                "justification": "space-between",
                "columnRatio": (1.0, 1.0),
            }
        ),
        copy.layoutId: copy.model_copy(
            update={
                "gridPlacement": GridPlacement(
                    row=0, column=1, rowSpan=1, columnSpan=1
                ),
                "lineHeightToken": "bodyLine",
                "textFitPolicy": "truncate",
            }
        ),
        photo.layoutId: photo.model_copy(
            update={
                "imageFit": "cover",
                "cropFocalPoint": FocalPoint(x=0.25, y=0.75),
            }
        ),
    }
    candidate_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "tokens": (*request.spec.tokens, ImportantToken(name="bodyLine", value=1.5)),
                "layout": request.spec.layout.model_copy(
                    update={
                        "children": tuple(
                            updated.get(node.layoutId, node)
                            for node in request.spec.layout.children
                        )
                    }
                ),
            }
        )
    )
    candidate_request = request.model_copy(update={"spec": candidate_spec})
    candidate = render_slidespec(candidate_request)

    assert "gap: 18px" in candidate.html
    assert "grid-template-columns: 1fr 1fr" in candidate.html
    assert "grid-column: 2 / span 1" in candidate.html
    assert "line-height: 1.5" in candidate.html
    assert "text-overflow: ellipsis" in candidate.html
    assert "object-fit: cover" in candidate.html
    assert "object-position: 25% 75%" in candidate.html

    measured = {
        "width": 1280,
        "height": 720,
        "elements": [
            {
                "qid": "grid",
                "bbox": {"x": 100, "y": 100, "width": 600, "height": 300},
                "computed_style": {
                    "gap": "18px",
                    "paddingTop": "30px",
                    "paddingRight": "30px",
                    "paddingBottom": "30px",
                    "paddingLeft": "30px",
                    "alignItems": "center",
                    "justifyContent": "space-between",
                    "gridTemplateColumns": "270px 270px",
                },
            },
            {
                "qid": "copy",
                "bbox": {"x": 120, "y": 120, "width": 200, "height": 80},
                "computed_style": {
                    "fontSize": "20px",
                    "lineHeight": "30px",
                    "gridColumn": "2 / span 1",
                    "gridRow": "1 / span 1",
                    "whiteSpace": "nowrap",
                    "overflow": "hidden",
                    "overflowWrap": "normal",
                    "textOverflow": "ellipsis",
                    "paddingTop": "0px",
                    "paddingRight": "0px",
                    "paddingBottom": "0px",
                    "paddingLeft": "0px",
                },
            },
            {
                "qid": "photo",
                "bbox": {"x": 340, "y": 120, "width": 300, "height": 180},
                "computed_style": {
                    "objectFit": "cover",
                    "objectPosition": "25% 75%",
                    "paddingTop": "0px",
                    "paddingRight": "0px",
                    "paddingBottom": "0px",
                    "paddingLeft": "0px",
                },
            },
        ],
    }
    assert imported_projection_roundtrip_errors(candidate_request, measured) == ()
    measured["elements"][0]["computed_style"]["gap"] = "10px"
    assert imported_projection_roundtrip_errors(candidate_request, measured) == (
        "projection.gap.mismatch:grid",
    )


def test_projection_fails_closed_for_static_move_and_connector_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = '<html><body><p data-qid="title">Hello</p></body></html>'
    metrics = _metrics()
    metrics["elements"] = [
        {
            **next(item for item in metrics["elements"] if item["qid"] == "title"),
            "computed_style": {
                **next(
                    item for item in metrics["elements"] if item["qid"] == "title"
                )["computed_style"],
                "position": "static",
            },
        }
    ]

    async def fake_render(_source: str) -> tuple[bytes, dict]:
        return b"png", metrics

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )
    request, _image, _evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )
    node = request.spec.layout.children[0]
    assert node.bounds is not None
    moved = node.model_copy(
        update={"bounds": node.bounds.model_copy(update={"x": node.bounds.x + 0.01})}
    )
    moved_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "layout": request.spec.layout.model_copy(update={"children": (moved,)})
            }
        )
    )
    with pytest.raises(SlideSpecError) as static_error:
        render_slidespec(request.model_copy(update={"spec": moved_spec}))
    assert static_error.value.code == "render.source_projection.position_unsupported"

    routed = node.model_copy(
        update={
            "connectorRoute": ConnectorRoute(
                points=(FocalPoint(x=0, y=0), FocalPoint(x=1, y=1))
            )
        }
    )
    routed_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "layout": request.spec.layout.model_copy(update={"children": (routed,)})
            }
        )
    )
    with pytest.raises(SlideSpecError) as connector_error:
        render_slidespec(request.model_copy(update={"spec": routed_spec}))
    assert connector_error.value.code == "render.source_projection.connector_unsupported"


def test_import_rejects_active_content_before_browser_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def should_not_render(_source: str) -> tuple[bytes, dict]:
        raise AssertionError("browser capture must not run for active content")

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        should_not_render,
    )
    with pytest.raises(SlideSpecImportError, match="import.source.active_content"):
        asyncio.run(
            build_authored_import_request(
                _slide("<html><body><script>alert(1)</script></body></html>")
            )
        )


def test_missing_active_bundle_is_reimported_instead_of_becoming_a_dangling_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slide = _slide("<html><body><p>restored</p></body></html>")
    slide.properties = {
        "slidespec_active": {
            "bundle_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "spec_hash": "sha256:missing",
        }
    }

    async def missing_bundle(_session, _slide):
        raise SlideSpecArtifactError("artifact.pointer.missing")

    async def attempted_reimport(_slide):
        raise SlideSpecImportError("import.reimport.attempted")

    class _Scalars:
        def all(self):
            return [slide]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Session:
        async def execute(self, _statement):
            return _Result()

    monkeypatch.setattr(
        "services.slidespec_authored_importer.load_active_slidespec_bundle",
        missing_bundle,
    )
    monkeypatch.setattr(
        "services.slidespec_authored_importer.build_authored_import_request",
        attempted_reimport,
    )
    monkeypatch.setattr(
        "services.slidespec_authored_importer.resolve_slidespec_repair_rollout",
        lambda *_args, **_kwargs: SimpleNamespace(
            route="slidespec_active", reason_code=None
        ),
    )

    result = asyncio.run(
        ensure_authored_slidespec_imports(
            presentation_id=slide.presentation,
            slides=[slide],
            positions=[0],
            opt_in=True,
            owner_id=None,
            session=_Session(),
        )
    )

    assert result.imported_positions == ()
    assert result.failures == {0: "import.reimport.attempted"}
