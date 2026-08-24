"""Focused product-path tests for capability admission and repeated Scene CAS."""

from __future__ import annotations

import base64
import hashlib

import pytest
from pydantic import ValidationError

from models.authored_vnext.deck import (
    SceneAssetManifestEntry,
    SceneDataManifestEntry,
    SceneEditability,
    SceneOwnerDecision,
    SceneRasterIsland,
    SceneRasterZBand,
    SceneShapeElement,
    SceneTextElement,
    SceneTextRun,
    SceneTextStyle,
    SlideHybridExportPlan,
    seal_deck_scene,
    seal_slide_scene,
    scene_digest,
)
from models.authored_vnext.master.contract import Rect
from services.authored_vnext.generation.scene_compiler import (
    apply_scene_element_mutation,
    default_scene_capabilities,
)
from services.authored_vnext.semantic.native_capabilities import (
    EDITABLE_MVP_CAPABILITY_IDS_V1,
    build_native_capability_projection_v1,
)


D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64


def _asset(name: str, content: bytes) -> SceneAssetManifestEntry:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    return SceneAssetManifestEntry(
        asset_id=f"asset:{name}",
        asset_digest=digest,
        media_type="image/png",
        storage_reference=(
            "data:image/png;base64," + base64.b64encode(content).decode("ascii")
        ),
        size_bytes=len(content),
    )


def _hybrid_scene():
    gradient_asset = _asset("gradient", b"gradient-raster-owner")
    chart_asset = _asset("chart", b"line-chart-raster-owner")
    elements = [
        SceneTextElement(
            kind="text",
            element_id="title.main",
            z_order=1,
            bounds=Rect(x=20, y=20, width=500, height=80),
            semantic_role="title",
            editability=SceneEditability(text=True, geometry=True, style=True),
            text="실제 제품 capability projection",
        ),
        SceneShapeElement(
            kind="shape",
            element_id="paint.gradient",
            z_order=2,
            bounds=Rect(x=20, y=120, width=300, height=120),
            semantic_role="body",
            editability=SceneEditability(),
            shape="rect",
        ),
        SceneShapeElement(
            kind="shape",
            element_id="chart.line",
            z_order=3,
            bounds=Rect(x=340, y=120, width=300, height=200),
            semantic_role="body",
            editability=SceneEditability(),
            shape="rect",
        ),
        SceneShapeElement(
            kind="shape",
            element_id="text.ja",
            z_order=4,
            bounds=Rect(x=20, y=340, width=620, height=100),
            semantic_role="body",
            editability=SceneEditability(),
            shape="rect",
        ),
    ]
    plan = SlideHybridExportPlan(
        owner_decisions=[
            SceneOwnerDecision(
                owner_id="title.main",
                decision="nativeEditable",
                capability_id="editable.text.ko-en.v1",
            ),
            SceneOwnerDecision(
                owner_id="paint.gradient",
                decision="rasterIsland",
                capability_id="css.gradient.v1",
                reason_code="gradient_receipt_missing",
            ),
            SceneOwnerDecision(
                owner_id="chart.line",
                decision="rasterIsland",
                capability_id="editable.chart.line-pie.v1",
                reason_code="chart_receipt_missing",
            ),
            SceneOwnerDecision(
                owner_id="text.ja",
                decision="publicationBlocked",
                capability_id="editable.text.ja-zh.v1",
                reason_code="scene_geometry_unavailable",
            ),
        ],
        raster_islands=[
            SceneRasterIsland(
                island_id="island:gradient",
                source_owner_ids=["paint.gradient"],
                asset_id=gradient_asset.asset_id,
                asset_digest=gradient_asset.asset_digest,
                bounds=elements[1].bounds,
                z_band=SceneRasterZBand(min_z=2, max_z=2),
                raster_plane_digest=gradient_asset.asset_digest,
            ),
            SceneRasterIsland(
                island_id="island:chart",
                source_owner_ids=["chart.line"],
                asset_id=chart_asset.asset_id,
                asset_digest=chart_asset.asset_digest,
                bounds=elements[2].bounds,
                z_band=SceneRasterZBand(min_z=3, max_z=3),
                raster_plane_digest=chart_asset.asset_digest,
            ),
        ],
    )
    slide = seal_slide_scene(
        slide_id="slide:capability",
        revision=1,
        elements=elements,
        hybrid_export=plan,
    )
    return seal_deck_scene(
        deck_id="deck:capability",
        revision=1,
        composition_digest=D0,
        source_digest=D1,
        hybrid_profile_id="authored-editable-hybrid-ko-en-basic-v1",
        slides=[slide],
        asset_manifest=[gradient_asset, chart_asset],
        capabilities=default_scene_capabilities(),
    )


def test_versioned_capability_projection_is_owner_closed_and_fail_closed() -> None:
    scene = _hybrid_scene()
    receipt = D2
    projection = build_native_capability_projection_v1(
        scene,
        ready_receipts={
            capability_id: receipt for capability_id in EDITABLE_MVP_CAPABILITY_IDS_V1
        },
    )

    admissions = {item.owner_id: item for item in projection.admissions}
    assert admissions["title.main"].owner_domain == "native"
    assert admissions["paint.gradient"].owner_domain == "raster"
    assert admissions["chart.line"].owner_domain == "raster"
    assert admissions["text.ja"].owner_domain == "blocked"
    readiness = {item.capability_id: item for item in projection.capabilities}
    assert readiness["editable.text.ko-en.v1"].status == "ready"
    assert readiness["css.gradient.v1"].status == "fidelityFallback"
    assert readiness["editable.chart.line-pie.v1"].ready is False
    assert readiness["editable.text.ja-zh.v1"].status == "blocked"
    assert projection.duplicate_owner_count == projection.missing_owner_count == 0
    assert projection.full_native_parity_ready is False
    assert projection.promotion_ready is False
    assert projection.deployment_authorized is False

    invalid = scene.slides[0].hybrid_export.owner_decisions[0].model_dump(
        mode="json", by_alias=True
    )
    invalid["reasonCode"] = "native_but_claimed_fallback"
    with pytest.raises(ValidationError, match="cannot carry a fallback reason"):
        SceneOwnerDecision.model_validate(invalid)

    advanced_text = SceneTextElement(
        kind="text",
        element_id="text.advanced",
        z_order=1,
        bounds=Rect(x=0, y=0, width=300, height=80),
        semantic_role="body",
        editability=SceneEditability(text=True, style=True),
        text="A😀B",
        style=SceneTextStyle(auto_fit="shrink"),
        runs=[
            SceneTextRun(run_id="run:a", text="A", font_weight=700),
            SceneTextRun(run_id="run:empty", text=""),
            SceneTextRun(run_id="run:surrogate", text="😀", italic=True),
            SceneTextRun(run_id="run:b", text="B", underline=True),
        ],
    )
    assert advanced_text.runs[1].text == ""
    assert advanced_text.style.auto_fit == "shrink"

    table_payload = {
        "cells": [["A", "B"], ["C", "D"]],
        "rowHeightsPt": [20, 24],
        "mergedCells": [
            {"row": 0, "column": 0, "rowSpan": 1, "columnSpan": 2}
        ],
    }
    table_entry = SceneDataManifestEntry(
        data_id="data:advanced-table",
        data_digest=scene_digest(table_payload),
        kind="table",
        payload=table_payload,
    )
    assert table_entry.payload["rowHeightsPt"] == [20, 24]

    chart_payload = {
        "categories": ["Q1", "Q2"],
        "series": [{"name": "매출", "values": [10, 20]}],
        "style": {
            "chartStyle": 2,
            "seriesColors": ["#1463FF"],
            "legend": False,
        },
    }
    chart_entry = SceneDataManifestEntry(
        data_id="data:line-chart",
        data_digest=scene_digest(chart_payload),
        kind="chart",
        payload=chart_payload,
    )
    assert chart_entry.payload["series"][0]["values"] == [10, 20]


def test_actual_scene_mutation_rejects_three_replays_of_the_same_stale_cas() -> None:
    scene = _hybrid_scene()
    slide = scene.slides[0]
    updated = apply_scene_element_mutation(
        scene,
        slide_id=slide.slide_id,
        element_id="title.main",
        mutation="text.replace",
        payload={"text": "first committed edit"},
        expected_scene_digest=scene.scene_digest,
        expected_slide_revision=slide.revision,
        expected_slide_digest=slide.slide_digest,
    )
    committed_digest = updated.scene_digest

    for _ in range(3):
        with pytest.raises(ValueError, match="deck Scene CAS failed"):
            apply_scene_element_mutation(
                updated,
                slide_id=slide.slide_id,
                element_id="title.main",
                mutation="text.replace",
                payload={"text": "stale replay must not publish"},
                expected_scene_digest=scene.scene_digest,
                expected_slide_revision=slide.revision,
                expected_slide_digest=slide.slide_digest,
            )
        assert updated.scene_digest == committed_digest
        assert updated.slides[0].revision == 2
        assert updated.slides[0].elements[0].text == "first committed edit"
