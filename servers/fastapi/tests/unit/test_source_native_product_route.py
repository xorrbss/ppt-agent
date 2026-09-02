from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
import uuid

import pytest
from lxml import html as lxml_html

from services.authored_vnext.composition import (
    SealedContentUnit,
    assert_composition_plan,
    build_composition_plan,
    build_content_integrity_page,
)
from services.authored_vnext.design_pack import DesignPackRegistry, ingest_design_pack
from services.authored_vnext.generation_route import (
    GenerationRouteError,
    design_pack_storage_root,
    resolve_generation_route,
)
from services.authored_vnext.narrative_ir.content_graph import (
    ContentGraphValidationError,
    seal_content_graph,
    seal_content_integrity_receipt,
)
from services.authored_vnext.source_native_generation import (
    _append_flow_text_units,
    _append_fixed_text_columns,
    _apply_composition_plan,
    _build_slide_quality_observation,
    _estimated_flow_block_height,
    _flow_text_css,
    _patch_exact_slot,
    _select_flow_table_layout,
)
from services.authored_vnext.deck_ir.product_adapter import (
    _generation_release_quality_receipts,
)
from services.authored_vnext.master.digests import canonical_digest as quality_digest
from services.authored_vnext.slide_quality_acceptance import (
    canonical_digest as slide_quality_digest,
    seal_slide_quality_acceptance,
)
from services.authored_evidence_ledger import digest_deck_htmls
from services.public_quality_receipt_v3_projection import (
    project_persisted_authored_quality_receipt_v3,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def test_source_native_fixed_columns_preserve_atomic_order_without_overflow_columns() -> None:
    units = [
        SealedContentUnit(
            content_unit_id=(
                "generated:001:key-message"
                if index == 0
                else f"page:paragraph-{index:03d}"
            ),
            content_digest="sha256:" + f"{index + 1:064x}",
            kind="text",
            required=True,
            text=("원문 제목" if index == 0 else "가" * 17),
            data=(),
            source_page_index=0,
            source_unit_index=index,
            asset_occurrence_id=None,
            asset_digest=None,
            source_ownership=(),
            semantic_role="title" if index == 0 else "body",
        )
        for index in range(28)
    ]
    parent = lxml_html.Element("div")
    parent.set("data-qid", "flow--columns")
    slot = {
        "bounds": {"width": 1100, "height": 469},
        "spacing": {
            "insetLeft": 8,
            "insetTop": 8,
            "insetRight": 8,
            "insetBottom": 8,
        },
        "capacityEnvelope": {"minimumTextScale": 0.7},
        "textStyle": {"fontSize": 22},
    }

    _append_fixed_text_columns(
        parent,
        units,
        slot=slot,
        adaptive_scale=1.0,
        role="body",
        columns_count=2,
        column_gap=20.0,
    )

    style = str(parent.get("style") or "")
    columns = list(parent)
    resolved_columns = len(columns)
    assert "column-count" not in style
    assert resolved_columns > 1
    assert f"grid-template-columns:repeat({resolved_columns},minmax(0,1fr))" in style
    assert "width:100%" in style and "overflow:hidden" in style
    assert len(columns) == resolved_columns
    assert 2 <= resolved_columns <= 4
    body_nodes = [
        node
        for column in columns
        for node in column.xpath(".//*[@data-source-boundary-kind='paragraph']")
    ]
    body_font_px = float(
        str(body_nodes[0].get("style")).split("font-size:", 1)[1].split("px", 1)[0]
    )
    assert 16.0 <= body_font_px <= 22.0
    effective_scale = body_font_px / 22.0
    column_width = (1084.0 - 20.0 * (resolved_columns - 1)) / resolved_columns
    by_id = {unit.content_unit_id: unit for unit in units}
    heights = [
        sum(
            _estimated_flow_block_height(
                [by_id[str(node.get("data-source-flow-unit-id"))]],
                slot=slot,
                adaptive_scale=effective_scale,
                role="body",
                column_width=column_width,
            )
            for node in column.xpath(".//*[@data-source-flow-unit-id]")
        )
        for column in columns
    ]
    assert max(heights) <= 453.0
    assert max(heights) - min(heights) < max(heights) * 0.2
    assert [
        str(node.get("data-source-flow-unit-id"))
        for column in columns
        for node in column.xpath(".//*[@data-source-flow-unit-id]")
    ] == [unit.content_unit_id for unit in units]
    title = parent.xpath(".//*[@data-source-flow-unit-id='generated:001:key-message']")
    assert len(title) == 1
    assert title[0].get("data-source-boundary-kind") == "title-candidate"
    assert "font-size:32.00px" in str(title[0].get("style") or "")


def test_source_native_mixed_layout_selects_vertical_row_and_preserves_images() -> None:
    text_units = [
        SealedContentUnit(
            content_unit_id=f"page:paragraph-{index + 1:03d}",
            content_digest="sha256:" + f"{index + 1:064x}",
            kind="text",
            required=True,
            text=("고밀도 한글 원문 문장 " * 4).strip(),
            data=(),
            source_page_index=0,
            source_unit_index=index,
            asset_occurrence_id=None,
            asset_digest=None,
            source_ownership=(),
            semantic_role="body",
        )
        for index in range(4)
    ]
    image_units = [
        SealedContentUnit(
            content_unit_id=f"page:image-{index + 1:03d}",
            content_digest="sha256:" + f"{index + 101:064x}",
            kind="image",
            required=True,
            text="",
            data=(),
            source_page_index=0,
            source_unit_index=index + len(text_units),
            asset_occurrence_id=f"occurrence-{index + 1:03d}",
            asset_digest="sha256:" + f"{index + 201:064x}",
            source_ownership=(),
            semantic_role="data",
        )
        for index in range(4)
    ]
    node = lxml_html.Element("div")
    node.set("data-qid", "mixed-flow")
    slot = {
        "sourceFlowLayout": "text",
        "sourceFlowRole": "body",
        "bounds": {"x": 40, "y": 40, "width": 1000, "height": 620},
        "spacing": {
            "insetLeft": 10,
            "insetRight": 10,
            "insetTop": 10,
            "insetBottom": 10,
        },
        "textStyle": {"fontSize": 8},
        "capacityEnvelope": {
            "maxCharacters": 10000,
            "maxLines": 1000,
            "contentArea": 10000000,
            "characterArea": 10,
            "minimumTextScale": 0.7,
        },
        "layoutHints": {"columnCount": 2},
    }
    assets = {
        str(unit.asset_occurrence_id): {
            "assetDigest": unit.asset_digest,
            "dataUrl": "data:image/png;base64,iVBORw0KGgo=",
        }
        for unit in image_units
    }

    _patch_exact_slot(
        node,
        [*text_units, *image_units],
        slot=slot,
        image_assets=assets,
    )

    wrapper = node.xpath(".//*[@data-source-mixed-layout]")[0]
    image_panel = wrapper.xpath(
        ".//*[@data-qid='mixed-flow--mixed-images']"
    )[0]
    images = image_panel.xpath(".//img[@data-source-block-id]")
    assert wrapper.get("data-source-mixed-layout") == "vertical"
    assert float(wrapper.get("data-source-mixed-text-scale")) == 1.0
    assert "grid-template-rows" in str(wrapper.get("style") or "")
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in str(
        image_panel.get("style") or ""
    )
    assert [image.get("data-source-block-id") for image in images] == [
        unit.asset_occurrence_id for unit in image_units
    ]
    assert len({image.get("data-source-block-id") for image in images}) == 4
    assert all(
        "font-size:16.00px" in str(item.get("style") or "")
        for item in wrapper.xpath(".//*[@data-source-flow-unit-id]")
    )


def test_source_native_table_cells_share_data_driven_estimator_columns() -> None:
    cells = [
        SealedContentUnit(
            content_unit_id=f"source-page-042:table-cell-{index + 1:03d}",
            content_digest="sha256:" + f"{index + 1:064x}",
            kind="text",
            required=True,
            text=f"표 셀 {index + 1:02d}",
            data=(),
            source_page_index=41,
            source_unit_index=index,
            asset_occurrence_id=None,
            asset_digest=None,
            source_ownership=(),
            semantic_role="body",
        )
        for index in range(36)
    ]
    slot = {
        "bounds": {"width": 1100, "height": 469},
        "spacing": {
            "insetLeft": 8,
            "insetTop": 8,
            "insetRight": 8,
            "insetBottom": 8,
        },
        "textStyle": {"fontSize": 22},
    }
    column_width = 1084.0
    selected_columns, selected_height = _select_flow_table_layout(
        cells,
        slot=slot,
        adaptive_scale=1.0,
        role="body",
        column_width=column_width,
    )

    parent = lxml_html.Element("div")
    _append_flow_text_units(
        parent,
        cells,
        slot=slot,
        adaptive_scale=1.0,
        role="body",
        column_width=column_width,
    )

    assert 4 < selected_columns <= 8
    assert _estimated_flow_block_height(
        cells,
        slot=slot,
        adaptive_scale=1.0,
        role="body",
        column_width=column_width,
    ) == selected_height
    grids = parent.xpath("./div[starts-with(@data-qid, 'source-flow-table:')]")
    assert len(grids) == 1
    assert (
        f"grid-template-columns:repeat({selected_columns},minmax(0,1fr))"
        in str(grids[0].get("style") or "")
    )
    assert len(grids[0].xpath("./span")) == 36


def test_source_native_flow_text_inherits_container_color_without_pack_override() -> None:
    inherited = _flow_text_css(
        {"textStyle": {"fontSize": 24}}, role="body", adaptive_scale=1.0
    )
    declared = _flow_text_css(
        {"textStyle": {"fontSize": 24, "color": "#F8FAFC"}},
        role="body",
        adaptive_scale=1.0,
    )

    assert "color:inherit" in inherited
    assert "color:#F8FAFC" in declared


def test_source_native_quality_observation_uses_occupied_flow_regions_and_actionable_dom_issues() -> None:
    slots = [
        {
            "sourceFlowLayout": "text",
            "acceptedKinds": ["text", "image"],
            "patchHandle": {"elementId": "flow"},
            "bounds": {"x": 10, "y": 10, "width": 600, "height": 500},
        },
        {
            "sourceFlowLayout": "text",
            "acceptedKinds": ["text"],
            "patchHandle": {"elementId": "empty"},
            "bounds": {"x": 700, "y": 10, "width": 400, "height": 500},
        },
    ]
    common = {
        "contrast_ratio": 4.5,
        "occluded_fraction": 1.0,
        "clipped": True,
        "glyph_clipped": True,
        "line_box_clipped": True,
        "viewport_intersection_ratio": 0.5,
    }
    metrics = {
        "viewport": {"width": 1280, "height": 720},
        "elements": [
            {"qid": "flow", "css_visible": True, "bbox": {"x": 10, "y": 10, "width": 600, "height": 500}},
            {"qid": "empty", "css_visible": False, "bbox": {"x": 0, "y": 0, "width": 0, "height": 0}},
            {**common, "qid": "source-flow:generated:001:key-message", "parent_qid": "flow--columns", "font_size": 40, "bbox": {"x": 20, "y": 20, "width": 240, "height": 52}},
            {**common, "qid": "source-flow:page:paragraph-001", "parent_qid": "flow--columns", "font_size": 16, "bbox": {"x": 330, "y": 20, "width": 240, "height": 120}},
            {"qid": "flow--mixed-images", "bbox": {"x": 310, "y": 100, "width": 300, "height": 300}},
            {
                "qid": "source-flow-image:occ-1",
                "parent_qid": "flow--mixed-images",
                "source_block_id": "occ-1",
                "viewport_intersection_ratio": 1.0,
                "bbox": {"x": 310, "y": 120, "width": 300, "height": 280},
                "image": {"complete": True, "naturalWidth": 1200, "naturalHeight": 800},
            },
        ],
    }

    observation = _build_slide_quality_observation(
        slide_index=0,
        slide_digest="sha256:" + "1" * 64,
        normalized_metrics=metrics,
        slots=slots,
        report=SimpleNamespace(issues=()),
        frame_role="content",
        expected_protected_digest="sha256:" + "2" * 64,
        observed_protected_digest="sha256:" + "2" * 64,
    )

    assert observation["readabilityScore"] == 1.0
    assert observation["titleBodyHierarchyScore"] == 1.0
    assert observation["clippedElementCount"] == 0
    assert observation["offCanvasElementCount"] == 0
    assert observation["meaningfulImageSizeScore"] == 1.0
    assert observation["undersizedMeaningfulImageCount"] == 0
    assert observation["templateSlotFidelityScore"] == 1.0
    assert observation["templateGeometryFidelityScore"] == 1.0


def test_source_native_composition_is_exact_protected_capacity_gated_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    owner_id = uuid.uuid4()
    token = set_current_owner_id(owner_id)
    try:
        manifest = ingest_design_pack(
            {
                "schemaVersion": "authored-design-source/v1",
                "canvas": {"width": 1280, "height": 720},
                "frames": [
                    {
                        "id": "slide-1",
                        "type": "html",
                        "role": "cover",
                        "html": "<html><body><div data-qid='chrome'><h1 data-qid='title'>OLD<span aria-hidden='true'></span></h1><p data-qid='body'>OLD BODY</p></div><span data-qid='logo'>DEVAX</span></body></html>",
                        "elements": [
                            {
                                "id": "title",
                                "kind": "text",
                                "editable": True,
                                "slotApproved": True,
                                "slotId": "slot:slide-1-title",
                                "semanticRole": "title",
                                "required": True,
                                "extractionConfidence": 1.0,
                                "capacityEnvelope": {
                                    "measurementBasis": "chromium-observed",
                                    "contentArea": 108000,
                                    "maxCharacters": 200,
                                    "maxLines": 3,
                                },
                                "bounds": {"x": 60, "y": 60, "width": 900, "height": 120},
                            },
                            {
                                "id": "body",
                                "kind": "text",
                                "editable": True,
                                "slotApproved": True,
                                "slotId": "slot:slide-1-body",
                                "semanticRole": "body",
                                "required": True,
                                "extractionConfidence": 1.0,
                                "capacityEnvelope": {
                                    "measurementBasis": "chromium-observed",
                                    "contentArea": 360000,
                                    "maxCharacters": 400,
                                    "maxLines": 8,
                                },
                                "bounds": {"x": 60, "y": 220, "width": 900, "height": 400},
                            },
                            {
                                "id": "logo",
                                "kind": "shape",
                                "editable": False,
                                "bounds": {"x": 1080, "y": 40, "width": 120, "height": 40},
                            },
                        ],
                    }
                ],
            },
            pack_id="devax-source-native",
            package_version="1.0.0",
            storage_root=design_pack_storage_root(owner_id),
        )
        resolution = resolve_generation_route(
            generation_mode="registered-template",
            design_pack_pin={
                "designPackId": manifest.pack_id,
                "version": 1,
                "digest": manifest.package_digest,
            },
            slide_count=1,
        )
        assert resolution == {
            "selectedRoute": "source-native",
            "designPackPin": {
                "designPackId": manifest.pack_id,
                "version": 1,
                "digest": manifest.package_digest,
            },
            "narrativeIrDigest": None,
            "fallbackReason": None,
        }

        ownership = [
            {
                "sourceBindingId": "source-binding-001",
                "sourceContentDigest": "sha256:" + "1" * 64,
                "useMode": "verbatim",
                "required": True,
            }
        ]
        graph = seal_content_graph(
            [
                {
                    "pageId": "page-001",
                    "sourceUnitId": "source-page-001",
                    "contentUnits": [
                        {
                            "contentUnitId": "unit-title",
                            "kind": "text",
                            "payload": {"text": "DevAX AI 개발 플랫폼"},
                            "required": True,
                            "sourceOwnership": ownership,
                        },
                        {
                            "contentUnitId": "unit-body-1",
                            "kind": "text",
                            "payload": {"text": "품질 우선 모듈 구조"},
                            "required": True,
                            "sourceOwnership": ownership,
                        },
                        {
                            "contentUnitId": "unit-body-2",
                            "kind": "text",
                            "payload": {"text": "안전한 배포 자동화"},
                            "required": True,
                            "sourceOwnership": ownership,
                        },
                    ],
                }
            ],
            generation_lane="new-authoring",
        )
        slide = SimpleNamespace(
            slide_key="slide-001",
            sequence_index=0,
            role="cover",
            key_message="DevAX AI 개발 플랫폼",
            supporting_points=["품질 우선 모듈 구조", "안전한 배포 자동화"],
            data=[],
            source_ownership=[],
            content_unit_ids=tuple(graph.required_content_unit_ids),
            sealed_content_units=graph.pages[0].content_units,
        )
        narrative = SimpleNamespace(
            content_graph_digest=graph.content_graph_digest,
            sealed_content_units=(),
        )
        package = DesignPackRegistry(design_pack_storage_root(owner_id)).get(
            manifest.pack_id, manifest.package_version
        )
        frame = package.structure.frames[0]
        plan, units, slots = build_composition_plan(
            narrative=narrative,
            slide=slide,
            frame=frame,
        )
        assert_composition_plan(plan)
        raw_html = "<html><body><div data-qid='chrome'><h1 data-qid='title'>OLD<span aria-hidden='true'></span></h1><p data-qid='body'>OLD BODY</p></div><span data-qid='logo'>DEVAX</span></body></html>"
        compiled, semantic, protected_before, protected_after = _apply_composition_plan(
            raw_html,
            plan=plan,
            units=units,
            slots=slots,
        )
        mapped_ids = [item["contentUnitId"] for item in plan["assignments"]]
        assert mapped_ids == list(graph.required_content_unit_ids)
        assert len(mapped_ids) == len(set(mapped_ids)) == 3
        assert protected_before == protected_after
        assert "data-qid=\"logo\">DEVAX" in compiled
        assert "aria-hidden=\"true\"" in compiled
        assert "OLD" not in compiled
        assert 'data-quality-unintentional="true"' in compiled
        assert "overflow-wrap:break-word" in compiled
        assert [item["qid"] for item in semantic] == ["body", "title"]

        metrics = {
            "elements": [
                {
                    "qid": qid,
                    "css_visible": True,
                    "effective_opacity": 1,
                    "viewport_intersection_ratio": 1,
                    "clipped": False,
                }
                for qid in ("title", "body", "chrome", "logo")
            ]
        }
        page = build_content_integrity_page(
            plan=plan,
            units=units,
            rendered_html=compiled,
            browser_metrics=metrics,
            protected_digest_before=protected_before,
            protected_digest_after=protected_after,
        )
        receipt = seal_content_integrity_receipt(
            operation_id=uuid.uuid4(),
            attempt_binding_digest="sha256:" + "5" * 64,
            source_revision=1,
            result_revision=2,
            generation_lane="new-authoring",
            content_graph_digest=graph.content_graph_digest,
            route_strategy_receipt_digest="sha256:" + "4" * 64,
            narrative_digest="sha256:" + "2" * 64,
            preserve_source_input_digest=None,
            provider_call_budget={
                "schemaVersion": "narrative-provider-call-budget/v1",
                "maximumLogicalCalls": 2,
                "maximumPhysicalCalls": 2,
                "actualLogicalCalls": 0,
                "actualPhysicalCalls": 0,
                "reservations": [],
            },
            pages=[page],
        )
        assert receipt.status == "passed"
        assert receipt.required_coverage == 1.0

        oversized = SimpleNamespace(
            **{
                **slide.__dict__,
                "content_unit_ids": (),
                "sealed_content_units": (),
                "supporting_points": ["가" * 1000],
            }
        )
        rejected, _, _ = build_composition_plan(
            narrative=SimpleNamespace(content_graph_digest="sha256:" + "3" * 64, sealed_content_units=()),
            slide=oversized,
            frame=frame,
        )
        assert rejected["status"] == "failed"
        assert rejected["unmappedRequiredUnits"]

        hidden_metrics = {
            "elements": [
                {
                    **item,
                    "css_visible": item["qid"] != "body",
                    "viewport_intersection_ratio": 0 if item["qid"] == "body" else 1,
                }
                for item in metrics["elements"]
            ]
        }
        hidden_page = build_content_integrity_page(
            plan=plan,
            units=units,
            rendered_html=compiled,
            browser_metrics=hidden_metrics,
            protected_digest_before=protected_before,
            protected_digest_after=protected_after,
        )
        with pytest.raises(
            ContentGraphValidationError,
            match="content_integrity_required_coverage_failed",
        ):
            seal_content_integrity_receipt(
                operation_id=uuid.uuid4(),
                attempt_binding_digest="sha256:" + "5" * 64,
                source_revision=1,
                result_revision=2,
                generation_lane="new-authoring",
                content_graph_digest=graph.content_graph_digest,
                route_strategy_receipt_digest="sha256:" + "4" * 64,
                narrative_digest="sha256:" + "2" * 64,
                preserve_source_input_digest=None,
                provider_call_budget={
                    "schemaVersion": "narrative-provider-call-budget/v1",
                    "maximumLogicalCalls": 2,
                    "maximumPhysicalCalls": 2,
                    "actualLogicalCalls": 0,
                    "actualPhysicalCalls": 0,
                    "reservations": [],
                },
                pages=[hidden_page],
            )

        with pytest.raises(GenerationRouteError, match="design_pack_pin_digest_mismatch"):
            resolve_generation_route(
                generation_mode="registered-template",
                design_pack_pin={
                    "designPackId": manifest.pack_id,
                    "version": 1,
                    "digest": "sha256:" + "0" * 64,
                },
                slide_count=1,
            )
    finally:
        reset_current_owner_id(token)


def test_source_native_provider_free_slide_quality_reaches_public_release_projection() -> None:
    protected_digest = slide_quality_digest({"protected": "chrome"})
    observations = [
        {
            "slideIndex": index,
            "slideDigest": slide_quality_digest({"slide": index}),
            "browserObserved": True,
            "browserEngine": "chromium",
            "viewport": {"width": 1600, "height": 900},
            "readabilityScore": 0.98,
            "titleBodyHierarchyScore": 0.98,
            "bodyTextElementCount": 2,
            "minimumBodyFontPt": 18.0,
            "templateMinimumBodyFontPt": 16.0,
            "wrappingViolationCount": 0,
            "clippedElementCount": 0,
            "prohibitedOverlapCount": 0,
            "offCanvasElementCount": 0,
            "balanceScore": 0.98,
            "whitespaceScore": 0.98,
            "meaningfulImageCount": 1,
            "meaningfulImageSizeScore": 0.98,
            "imageCropFidelityScore": 0.98,
            "undersizedMeaningfulImageCount": 0,
            "badImageCropCount": 0,
            "templateSlotFidelityScore": 0.98,
            "templateGeometryFidelityScore": 0.98,
            "expectedProtectedChromeDigest": protected_digest,
            "observedProtectedChromeDigest": protected_digest,
        }
        for index in range(2)
    ]
    slide_quality = seal_slide_quality_acceptance(
        subject_digest=slide_quality_digest({"subject": "source-native"}),
        browser_observations=observations,
        expected_slide_count=2,
    ).to_dict()
    inventory = {
        "schemaVersion": "source-content-text-image-inventory/v1",
        "sourceMode": "text-image",
        "sourceCompleteClaim": False,
        "preserved": ["text", "image-occurrences"],
        "excluded": ["tables", "charts", "original-layout"],
        "excludedSourceObjectCounts": {"tables": 0, "charts": 0, "original-layout": 2},
        "textOccurrences": [
            {"sourceBlockId": "text-1", "contentDigest": "sha256:" + "1" * 64},
            {"sourceBlockId": "text-2", "contentDigest": "sha256:" + "2" * 64},
        ],
        "imageOccurrences": [
            {"sourceBlockId": "image-1", "contentDigest": "sha256:" + "3" * 64}
        ],
    }
    source_content = {
        **inventory,
        "textOccurrenceCount": 2,
        "imageOccurrenceCount": 1,
        "occurrenceInventoryDigest": quality_digest(inventory),
        "status": "passed",
        "required": True,
    }
    htmls = ["<html><body>one</body></html>", "<html><body>two</body></html>"]
    v2, v3, evidence = _generation_release_quality_receipts(
        attempt_token="source-native-attempt",
        revision=2,
        htmls=htmls,
        deck_ir={
            "slides": [
                {"role": "content", "components": []},
                {"role": "content", "components": []},
            ]
        },
        generation_quality=slide_quality,
        content_integrity={
            "status": "passed",
            "requiredCoverage": 1.0,
            "receiptDigest": "sha256:" + "4" * 64,
        },
        source_content_quality_evidence=source_content,
    )

    scene_digest = digest_deck_htmls(htmls)
    presentation_id = str(uuid.uuid4())
    projected = project_persisted_authored_quality_receipt_v3(
        authored_quality_gate={**v2.to_dict(), "qualityReceiptV3": v3.to_dict()},
        quality_receipt_id="quality-source-native",
        operation_id=str(uuid.uuid4()),
        presentation_id=presentation_id,
        artifact_digest="sha256:" + "5" * 64,
        source_revision=2,
        scene_digest=scene_digest,
        exporter_version="fidelity-test/v1",
        evaluated_at=datetime.now(timezone.utc),
        generation_evidence_record={
            "schemaVersion": "quality-receipt-event/v3",
            "presentationId": presentation_id,
            "resultRevision": 2,
            "receipt": v3.to_dict(),
            "evidence": evidence,
        },
        render_runtime_binding_digest="sha256:" + "6" * 64,
        result_render_identity_digest="sha256:" + "7" * 64,
    )

    assert v2.require_source_readback is True
    assert v2.require_visual_review is True
    assert not v3.blockers
    assert projected.image_content_integrity.status == "passed"
    assert projected.visual_review.status == "passed"
    assert projected.release_readiness == "ready"
