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
                "offset_parent_qid": "root",
                "containing_block_proof": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "exactBoundOffsetParent": True,
                    "offsetParentQid": "root",
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


def test_import_baseline_preserves_padding_for_partially_off_canvas_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<section data-qid="root">'
        '<footer data-qid="footer" style="padding-top: 9.8812px">Hello</footer>'
        "</section>"
    )
    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "root",
                "tag": "section",
                "bbox": {"x": 0, "y": 0, "width": 1280, "height": 720},
                "client_width": 1280,
                "client_height": 720,
                "scroll_width": 1280,
                "scroll_height": 720,
                "text": "Hello",
                "computed_style": {"position": "relative", "display": "block"},
            },
            {
                "qid": "footer",
                "parent_qid": "root",
                "tag": "footer",
                # The full source box extends ten pixels beyond the canvas.
                # SlideSpec bounds are clipped, but CSS padding is still relative
                # to this complete twenty-pixel source box.
                "bbox": {"x": 48, "y": 710, "width": 1184, "height": 20},
                "client_width": 1184,
                "client_height": 20,
                "scroll_width": 1184,
                "scroll_height": 20,
                "font_size": 9,
                "text": "Hello",
                "computed_style": {
                    "position": "static",
                    "display": "flex",
                    "fontSize": "9px",
                    "paddingTop": "9.8812px",
                    "paddingRight": "0px",
                    "paddingBottom": "0px",
                    "paddingLeft": "0px",
                },
                "static_translation_probe": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "position": "static",
                    "transformComposable": True,
                    "layoutIsolation": True,
                    "flowImpactPx": 0,
                    "maxSiblingDeltaPx": 0,
                },
            },
        ],
    }

    async def fake_render(source: str) -> tuple[bytes, dict]:
        assert source == html
        return b"png", metrics

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )
    request, _image, evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )

    baseline = render_slidespec(request)
    assert baseline.html == html
    assert evidence["render_parity"] == "byte_identical_baseline"


def test_projection_preserves_right_and_bottom_position_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<!doctype html><html><body><section data-qid="root">'
        '<footer data-qid="title" '
        'style="position:fixed;right:32px;bottom:-14px;width:400px;height:60px">'
        'Hello</footer></section></body></html>'
    )
    metrics = _metrics()
    title = next(item for item in metrics["elements"] if item["qid"] == "title")
    title["bbox"] = {"x": 848, "y": 674, "width": 400, "height": 60}
    title["computed_style"] = {
        **title["computed_style"],
        "position": "fixed",
        "left": "auto",
        "top": "auto",
        "right": "32px",
        "bottom": "-14px",
    }

    async def fake_render(source: str) -> tuple[bytes, dict]:
        assert source == html
        return b"png", metrics

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )
    request, _image, _evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )
    assert request.source is not None
    binding = next(item for item in request.source.bindings if item.qid == "title")
    assert binding.leftPx is None
    assert binding.topPx is None
    assert binding.rightPx == 32
    assert binding.bottomPx == -14

    node = next(
        child for child in request.spec.layout.children
        if child.layoutId == binding.layoutId
    )
    assert node.bounds is not None
    moved = node.model_copy(
        update={
            "bounds": node.bounds.model_copy(
                update={
                    "x": node.bounds.x - 20 / 1280,
                    "y": node.bounds.y - 14 / 720,
                }
            )
        }
    )
    moved_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "layout": request.spec.layout.model_copy(
                    update={
                        "children": tuple(
                            moved if child.layoutId == node.layoutId else child
                            for child in request.spec.layout.children
                        )
                    }
                )
            }
        )
    )
    candidate = render_slidespec(request.model_copy(update={"spec": moved_spec}))

    assert "right: 52.0000px" in candidate.html
    assert "bottom: 0.0000px" in candidate.html
    assert "left:" not in candidate.html
    assert "top:" not in candidate.html


def test_importer_v4_infers_only_baseline_supported_relational_intent(
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
            "offset_parent_qid": "root",
            "containing_block_proof": {
                "source": "chrome",
                "telemetryComplete": True,
                "exactBoundOffsetParent": True,
                "offsetParentQid": "root",
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
    assert request.source.importerVersion == "authored-html-import/4"
    assert request.spec.provenance.adapterVersion == "authored-html-import/4"
    assert {constraint.kind for constraint in request.spec.constraints} == {
        "anchor",
        "alignment",
        "gap",
        "containment",
        "nonOverlap",
        "order",
        "readability",
    }
    assert evidence["constraint_count"] == len(request.spec.constraints)
    by_qid = {binding.qid: binding for binding in request.source.bindings}
    assert all(
        constraint.kind != "group" for constraint in request.spec.constraints
    )
    assert evidence["constraint_inference"]["omitted"][
        "implicit_group_without_explicit_evidence"
    ] == 1
    anchors = [
        constraint
        for constraint in request.spec.constraints
        if constraint.kind == "anchor"
    ]
    assert anchors
    assert {constraint.container for constraint in anchors} == {
        by_qid["root"].layoutId
    }
    gaps = [
        constraint
        for constraint in request.spec.constraints
        if constraint.kind == "gap"
    ]
    assert gaps and all(constraint.preferred is not None for constraint in gaps)
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

    # A confirmed owned-paint defect makes its ancestry branch an invalid
    # source for aesthetic alignment/gap preferences. Structural hard
    # non-overlap/order constraints remain available.
    by_metric_qid = {item["qid"]: item for item in metrics["elements"]}
    by_metric_qid["body"].update(
        {
            "has_owned_paint": True,
            "occluded_fraction": 1.0,
            "continuous_occluded_fraction": 1.0,
            "occlusion_probe_version": "owned-paint-v3",
            "occlusion_occluder_qids": ["title"],
            "occlusion_unbound_count": 0,
        }
    )
    by_metric_qid["title"]["has_owned_paint"] = True
    defective_request, _defective_image, defective_evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )
    assert not {
        "alignment",
        "gap",
        "group",
    }.intersection(
        constraint.kind for constraint in defective_request.spec.constraints
    )
    omitted = defective_evidence["constraint_inference"]["omitted"]
    assert omitted["alignment_on_defective_branch"] >= 1
    assert omitted["preferred_gap_on_defective_branch"] >= 1


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


def test_roundtrip_preserves_raw_overhang_and_inherits_parent_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<html><body><footer data-qid="footer">'
        '<span data-qid="left">Left footer</span>'
        '<span data-qid="right">Right footer</span>'
        '</footer></body></html>'
    )
    zero_padding = {
        "paddingTop": "0px",
        "paddingRight": "0px",
        "paddingBottom": "0px",
        "paddingLeft": "0px",
    }
    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "footer",
                "tag": "footer",
                "bbox": {"x": 48, "y": 714, "width": 1184, "height": 20},
                "client_width": 1184,
                "client_height": 20,
                "scroll_width": 1184,
                "scroll_height": 20,
                "computed_style": {
                    **zero_padding,
                    "position": "static",
                    "display": "flex",
                    "alignItems": "baseline",
                    "justifyContent": "space-between",
                },
            },
            {
                "qid": "left",
                "parent_qid": "footer",
                "tag": "span",
                "bbox": {"x": 48, "y": 721, "width": 140, "height": 13},
                "client_width": 140,
                "client_height": 13,
                "scroll_width": 140,
                "scroll_height": 13,
                "font_size": 9,
                "text": "Left footer",
                "computed_style": {
                    **zero_padding,
                    "position": "static",
                    "display": "block",
                    "fontSize": "9px",
                    "alignItems": "normal",
                    "justifyContent": "normal",
                },
            },
            {
                "qid": "right",
                "parent_qid": "footer",
                "tag": "span",
                "bbox": {"x": 1152, "y": 721, "width": 80, "height": 13},
                "client_width": 80,
                "client_height": 13,
                "scroll_width": 80,
                "scroll_height": 13,
                "font_size": 9,
                "text": "Right footer",
                "computed_style": {
                    **zero_padding,
                    "position": "static",
                    "display": "block",
                    "fontSize": "9px",
                    "alignItems": "normal",
                    "justifyContent": "normal",
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
    request, _image, _evidence = asyncio.run(build_authored_import_request(_slide(html)))
    assert request.source is not None
    by_qid = {binding.qid: binding for binding in request.source.bindings}
    children = {node.layoutId: node for node in request.spec.layout.children}
    footer = children[by_qid["footer"].layoutId]
    assert footer.bounds is not None
    moved_footer = footer.model_copy(
        update={
            "bounds": footer.bounds.model_copy(
                update={"y": footer.bounds.y - 14 / 720}
            )
        }
    )
    moved_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "layout": request.spec.layout.model_copy(
                    update={
                        "children": tuple(
                            moved_footer if node.layoutId == footer.layoutId else node
                            for node in request.spec.layout.children
                        )
                    }
                )
            }
        )
    )
    candidate_request = request.model_copy(update={"spec": moved_spec})
    measured = {
        **metrics,
        "elements": [
            {
                **item,
                "bbox": {
                    **item["bbox"],
                    "y": item["bbox"]["y"] - 14,
                },
            }
            for item in metrics["elements"]
        ],
    }

    assert imported_projection_roundtrip_errors(candidate_request, measured) == ()


def test_roundtrip_accepts_size_preserving_child_reflow_under_resized_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<html><body><section data-qid="band" style="position:absolute">'
        '<span data-qid="copy">Copy</span></section></body></html>'
    )
    zero_padding = {
        "paddingTop": "0px",
        "paddingRight": "0px",
        "paddingBottom": "0px",
        "paddingLeft": "0px",
    }
    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "band",
                "tag": "section",
                "bbox": {"x": 75, "y": 580, "width": 1130, "height": 60},
                "computed_style": {
                    **zero_padding,
                    "position": "absolute",
                    "display": "flex",
                    "left": "75px",
                    "top": "580px",
                },
            },
            {
                "qid": "copy",
                "parent_qid": "band",
                "tag": "span",
                "bbox": {"x": 100, "y": 595, "width": 100, "height": 20},
                "text": "Copy",
                "glyph_clipped": False,
                "computed_style": {
                    **zero_padding,
                    "position": "static",
                    "display": "block",
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
    request, _image, _evidence = asyncio.run(build_authored_import_request(_slide(html)))
    assert request.source is not None
    by_qid = {binding.qid: binding for binding in request.source.bindings}
    children = {node.layoutId: node for node in request.spec.layout.children}
    band = children[by_qid["band"].layoutId]
    assert band.bounds is not None
    resized = band.model_copy(
        update={
            "bounds": band.bounds.model_copy(
                update={"y": 600 / 720, "height": 40 / 720}
            )
        }
    )
    candidate_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "layout": request.spec.layout.model_copy(
                    update={
                        "children": tuple(
                            resized if node.layoutId == band.layoutId else node
                            for node in request.spec.layout.children
                        )
                    }
                )
            }
        )
    )
    candidate_request = request.model_copy(update={"spec": candidate_spec})
    measured = {
        **metrics,
        "elements": [
            {
                **metrics["elements"][0],
                "bbox": {"x": 75, "y": 600, "width": 1130, "height": 40},
            },
            {
                **metrics["elements"][1],
                "bbox": {"x": 100, "y": 610, "width": 100, "height": 20},
            },
        ],
    }

    assert imported_projection_roundtrip_errors(candidate_request, measured) == ()
    measured["elements"][1]["bbox"]["width"] = 90
    assert imported_projection_roundtrip_errors(candidate_request, measured) == (
        "projection.bounds.mismatch:copy",
    )


def test_set_bounds_projects_through_authored_max_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = '<section data-qid="root" style="position:relative;max-height:260px">Body</section>'
    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "root",
                "tag": "section",
                "bbox": {"x": 48, "y": 200, "width": 1184, "height": 260},
                "client_width": 1184,
                "client_height": 260,
                "scroll_width": 1184,
                "scroll_height": 260,
                "text": "Body",
                "computed_style": {
                    "position": "relative",
                    "display": "block",
                    "maxHeight": "260px",
                    "paddingTop": "0px",
                    "paddingRight": "0px",
                    "paddingBottom": "0px",
                    "paddingLeft": "0px",
                },
            }
        ],
    }

    async def fake_render(_source: str) -> tuple[bytes, dict]:
        return b"png", metrics

    monkeypatch.setattr(
        "services.slidespec_authored_importer.render_html_to_png_with_metrics",
        fake_render,
    )
    request, _image, _evidence = asyncio.run(build_authored_import_request(_slide(html)))
    node = request.spec.layout.children[0]
    assert node.bounds is not None
    resized = node.model_copy(
        update={
            "bounds": node.bounds.model_copy(
                update={"height": node.bounds.height + 40 / 720}
            )
        }
    )
    resized_spec = seal_slidespec(
        request.spec.model_copy(
            update={
                "layout": request.spec.layout.model_copy(update={"children": (resized,)})
            }
        )
    )

    rendered = render_slidespec(request.model_copy(update={"spec": resized_spec}))

    assert "height: 300.0000px" in rendered.html
    assert "max-height: 300.0000px" in rendered.html


def test_importer_disallows_unproven_resize_for_in_flow_flex_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = (
        '<main data-qid="root" style="display:flex">'
        '<section data-qid="flow" style="position:relative">Flow</section>'
        '<aside data-qid="absolute" style="position:absolute">Absolute</aside>'
        "</main>"
    )
    zero_padding = {
        "paddingTop": "0px",
        "paddingRight": "0px",
        "paddingBottom": "0px",
        "paddingLeft": "0px",
    }
    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "root",
                "tag": "main",
                "bbox": {"x": 0, "y": 0, "width": 1280, "height": 720},
                "client_width": 1280,
                "client_height": 720,
                "scroll_width": 1280,
                "scroll_height": 720,
                "computed_style": {
                    **zero_padding,
                    "position": "relative",
                    "display": "flex",
                },
            },
            {
                "qid": "flow",
                "parent_qid": "root",
                "tag": "section",
                "bbox": {"x": 48, "y": 200, "width": 1184, "height": 260},
                "client_width": 1184,
                "client_height": 260,
                "scroll_width": 1184,
                "scroll_height": 260,
                "font_size": 18,
                "text": "Flow",
                "computed_style": {
                    **zero_padding,
                    "position": "relative",
                    "display": "block",
                    "fontSize": "18px",
                },
            },
            {
                "qid": "absolute",
                "parent_qid": "root",
                "tag": "aside",
                "bbox": {"x": 48, "y": 500, "width": 300, "height": 80},
                "client_width": 300,
                "client_height": 80,
                "scroll_width": 300,
                "scroll_height": 80,
                "font_size": 18,
                "text": "Absolute",
                "computed_style": {
                    **zero_padding,
                    "position": "absolute",
                    "display": "block",
                    "fontSize": "18px",
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
    request, _image, evidence = asyncio.run(
        build_authored_import_request(_slide(html))
    )
    assert request.source is not None
    bindings = {binding.qid: binding for binding in request.source.bindings}
    layouts = {
        node.layoutId: node for node in request.spec.layout.children
    }
    flow = layouts[bindings["flow"].layoutId]
    absolute = layouts[bindings["absolute"].layoutId]

    assert flow.constraints is not None
    assert flow.constraints.lockSize is True
    assert "setBounds" not in flow.constraints.allowedOperations
    assert "resizeWithinBounds" not in flow.constraints.allowedOperations
    assert "translateWithinParent" in flow.constraints.allowedOperations
    assert absolute.constraints is not None
    assert absolute.constraints.lockSize is False
    assert "setBounds" in absolute.constraints.allowedOperations
    assert "resizeWithinBounds" in absolute.constraints.allowedOperations
    assert evidence["projection_rejections"] == [
        {
            "reason_code": "repair.mutation_disallowed.resize_flow_unproven",
            "element": {
                "semantic_id": bindings["flow"].semanticId,
                "qid": "flow",
                "layout_id": bindings["flow"].layoutId,
            },
            "stage": "import_preflight",
            "metric": "resize_isolation",
            "value": "unproven",
            "parent_display": "flex",
            "position": "relative",
        }
    ]


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
