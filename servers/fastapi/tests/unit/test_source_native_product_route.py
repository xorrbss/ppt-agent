from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from services.authored_vnext.composition import (
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
from services.authored_vnext.source_native_generation import _apply_composition_plan
from utils.owner_context import reset_current_owner_id, set_current_owner_id


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
            generation_lane="new-authoring",
            content_graph_digest=graph.content_graph_digest,
            narrative_digest="sha256:" + "2" * 64,
            pages=[page],
        )
        assert receipt.status == "passed"
        assert receipt.required_coverage == 1.0

        oversized = SimpleNamespace(
            **{
                **slide.__dict__,
                "content_unit_ids": (),
                "sealed_content_units": (),
                "supporting_points": ["가" * 500],
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
                generation_lane="new-authoring",
                content_graph_digest=graph.content_graph_digest,
                narrative_digest="sha256:" + "2" * 64,
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
